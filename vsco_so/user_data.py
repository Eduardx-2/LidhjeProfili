import base64
import json
import re
import warnings
from contextlib import ExitStack
from queue import Empty, Queue
from urllib.parse import parse_qs, urlsplit
from bs4 import BeautifulSoup
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.bidi.common import command_builder
from selenium.webdriver.remote.websocket_connection import WebSocketConnection
from selenium.webdriver.support.ui import WebDriverWait

WAIT_SECONDS = 30

ASSIGNMENT = re.compile(r"\bwindow\.__PRELOADED_STATE__\s*=")

JS_TOKENS = re.compile(
    r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?<![\w$])undefined(?![\w$])''',
    re.DOTALL,
)

class ProfileExtractionError(RuntimeError):
    """Error sin incluir el HTML, las cabeceras ni el token en el mensaje."""

def _state_object(html):
    for script in BeautifulSoup(html, "html.parser").find_all("script"):
        content = script.get_text()
        assignment = ASSIGNMENT.search(content)
        if assignment:
            source = content[assignment.end():].lstrip()
            source = JS_TOKENS.sub(
                lambda match: "null" if match[0] == "undefined" else match[0], source
            )
            try:
                state, _ = json.JSONDecoder().raw_decode(source)
            except json.JSONDecodeError:
                raise ProfileExtractionError(
                    "El objeto __PRELOADED_STATE__ no es JSON válido tras normalizar undefined."
                ) from None
            if not isinstance(state, dict):
                raise ProfileExtractionError("__PRELOADED_STATE__ no contiene un objeto.")
            return state
    raise ProfileExtractionError("No se encontró la asignación window.__PRELOADED_STATE__.")

class _RawEvent:
    
    def __init__(self, name):
        self.event_class = name

    @staticmethod
    def from_json(params):
        return params

def _media_site(request):
    url = urlsplit(request.get("url", ""))
    host = url.hostname or ""
    if (
        request.get("method") != "GET"
        or not (host == "vsco.co" or host.endswith(".vsco.co"))
        or url.path.rstrip("/") != "/api/3.0/medias/profile"
    ):
        return None
    values = parse_qs(url.query).get("site_id", [])
    return values[0] if len(values) == 1 else None

class UserId:
    def __init__(self,driver,uri):
        self.using = UserSession()
        self.driver = driver
        self.uri = uri

    @property
    def session_cookie_web(self):
        self.using.req_profile(self.driver, self.uri)
        return self.current_cookie_web(required=True)

    def current_cookie_web(self, *, required=False):
        try:
            cookies = self.driver.get_cookies()
        except WebDriverException:
            raise ProfileExtractionError("Firefox no pudo obtener las cookies del perfil.") from None
        if not isinstance(cookies, list):
            raise ProfileExtractionError("Firefox devolvió un formato de cookies inválido.")
        values = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                raise ProfileExtractionError("Firefox devolvió una cookie inválida.")
            name, value = cookie.get("name"), cookie.get("value")
            if (not isinstance(name, str) or not name
                    or not isinstance(value, str)
                    or any(char in name for char in "=;\r\n")
                    or any(char in value for char in ";\r\n")):
                raise ProfileExtractionError("Firefox devolvió una cookie inválida.")
            values.append(f"{name}={value}")
        if required and not values:
            raise ProfileExtractionError("No se obtuvieron cookies nuevas para el perfil.")
        return "; ".join(values)

    def extract_info_id_token(self):
        """Obtiene los datos de sesión y agrega authorization listo para HTTP."""
        data = self.using.extract_info_id_token(self.driver, self.uri)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str):
            raise ProfileExtractionError("No se obtuvo un token válido para el perfil.")
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or token.lower() == "bearer" or any(char.isspace() for char in token):
            raise ProfileExtractionError("No se obtuvo un token válido para el perfil.")
        result = {**data, "token": token, "authorization": f"Bearer {token}"}
        # La cookie de la petición capturada puede diferir de get_cookies()
        # después de que los demás recursos del perfil hayan terminado de cargar.
        if "media_request_cookie" in data:
            cookie = data["media_request_cookie"]
            if not isinstance(cookie, str) or "\r" in cookie or "\n" in cookie:
                raise ProfileExtractionError("La petición del perfil contiene cookies inválidas.")
            result["cookie"] = cookie
        return result

    @property
    def idUser(self):
        state = _state_object(self.using.req_profile(self.driver, self.uri))
        username = urlsplit(self.uri).path.strip("/").split("/")[0]
        return state["sites"]["siteByUsername"][username]["site"]["id"]

class UserSession:
    def __init__(self):
        #self.URI = ""
        self.opt = Options()
        self.opt.enable_bidi = True


    def req_profile(self, driver, profile_url):
        
        challenge_active = False

        def ready(browser):
            nonlocal challenge_active
            html = browser.page_source
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.get_text().strip().lower() if soup.title else ""
            challenge_active = bool(
                title in {"just a moment...", "attention required! | cloudflare"}
                or soup.select_one('#challenge-running, #challenge-stage, form#challenge-form')
            )
            if challenge_active:
                # La pantalla puede ser transitoria dejar que el navegador cargue el perfil durante el mismo plazo que el resto del HTML.
                return False
            if any(ASSIGNMENT.search(script.get_text()) for script in soup.find_all("script")):
                return html
            return False

        try:
            driver.get(profile_url)
        except TimeoutException:
            # Puede haber HTML utilizable aunque otros recursos sigan cargando.
            pass
        except WebDriverException:
            raise ProfileExtractionError("Firefox no pudo abrir la URL del perfil.") from None
        try:
            return WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.2).until(ready)
        except TimeoutException:
            if challenge_active:
                raise ProfileExtractionError(
                    f"Cloudflare sigue mostrando un desafío tras {WAIT_SECONDS} segundos; "
                    "no se pudo acceder a la galería."
                ) from None
            raise ProfileExtractionError(
                f"No apareció window.__PRELOADED_STATE__ en {WAIT_SECONDS} segundos."
            ) from None

    def extract_info_id_token(self, driver, profile_url):
        
        websocket_url = driver.capabilities.get("webSocketUrl")
        if not websocket_url:
            raise ProfileExtractionError("Crea Firefox con options.enable_bidi = True.")

        queue = Queue()
        requests, responses, failures = {}, {}, set()

        with ExitStack() as cleanup:
            try:
                connection = WebSocketConnection(websocket_url, timeout=WAIT_SECONDS, interval=0.1)
            except WebDriverException:
                raise ProfileExtractionError("No se pudo conectar al WebSocket BiDi de Firefox.") from None
            cleanup.callback(connection.close)

            def command(name, **params):
                return connection.execute(command_builder(name, params))

            def release(name, **params):
                try:
                    command(name, **params)
                except WebDriverException:
                    warnings.warn("No se pudo liberar un recurso de captura BiDi.", RuntimeWarning)

            # El recolector y las suscripciones existen ANTES de driver.get().
            try:
                collector = command(
                    "network.addDataCollector", dataTypes=["response"],
                    maxEncodedDataSize=16 * 1024 * 1024,
                    collectorType="blob", contexts=[driver.current_window_handle],
                )["collector"]
            except WebDriverException:
                raise ProfileExtractionError(
                    "Firefox no permite network.addDataCollector; actualiza Firefox/geckodriver "
                    "a una versión con captura de cuerpos BiDi."
                ) from None
            cleanup.callback(release, "network.removeDataCollector", collector=collector)
            events = ["network.beforeRequestSent", "network.responseCompleted", "network.fetchError"]
            for name in events:
                event = _RawEvent(name)
                callback_id = connection.add_callback(
                    event, lambda params, name=name: queue.put((name, params))
                )
                cleanup.callback(connection.remove_callback, event, callback_id)
            try:
                subscription = command(
                    "session.subscribe", events=events, contexts=[driver.current_window_handle]
                )["subscription"]
            except WebDriverException:
                raise ProfileExtractionError("No se pudo registrar la captura de red BiDi.") from None
            cleanup.callback(release, "session.unsubscribe", subscriptions=[subscription])

            state = _state_object(self.req_profile(driver, profile_url))
            try:
                user = state["users"]["currentUser"]
                token = user["tkn"]
                by_site = state["medias"]["bySiteId"]
            except (KeyError, TypeError):
                raise ProfileExtractionError("Faltan users.currentUser o medias.bySiteId en el estado.") from None
            if not isinstance(token, str) or not token:
                raise ProfileExtractionError("users.currentUser.tkn no contiene un token.")
            if not isinstance(by_site, dict) or not by_site:
                raise ProfileExtractionError("medias.bySiteId no contiene ninguna galería.")
            sites = {str(site) for site in by_site}

            def initial_response(_):
                while True:
                    try:
                        name, params = queue.get_nowait()
                    except Empty:
                        break
                    request = params.get("request", {})
                    request_id = request.get("request")
                    site = _media_site(request)
                    if site not in sites or request_id is None:
                        continue
                    if name == "network.beforeRequestSent":
                        cookie = ""
                        for header in request.get("headers", []):
                            if header["name"].lower() == "cookie":
                                value = header["value"]
                                cookie = (base64.b64decode(value["value"]).decode("utf-8")
                                          if value["type"] == "base64" else value["value"])
                                break
                        requests.setdefault(request_id, (
                            params["timestamp"], site, request.get("url"), cookie,
                        ))
                    elif name == "network.responseCompleted":
                        responses[request_id] = params["response"]
                    elif name == "network.fetchError":
                        failures.add(request_id)
                if not requests:
                    return False
                first = min(requests, key=lambda key: requests[key][0])
                return first if first in responses or first in failures else False

            try:
                request_id = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.2).until(initial_response)
            except TimeoutException:
                request_id = min(requests, key=lambda key: requests[key][0]) if requests else None
                warnings.warn("No se recibió la respuesta inicial de medias/profile a tiempo.", RuntimeWarning)

            site_id = requests[request_id][1] if request_id else None
            if site_id is None:
                if len(sites) != 1:
                    raise ProfileExtractionError(
                        "Hay varios site_id y no se capturó la solicitud inicial para desambiguarlos."
                    )
                site_id = next(iter(sites))
            response = responses.get(request_id, {})
            status = response.get("status")
            result = dict(token=token, site_id=site_id, media_request_status=status)
            if request_id:
                result["media_request_url"] = requests[request_id][2]
                result["media_request_cookie"] = requests[request_id][3]
            if status is None or not 200 <= status < 300:
                if request_id in failures:
                    warnings.warn("La solicitud inicial de medias/profile falló sin respuesta HTTP.", RuntimeWarning)
                return result
            try:
                body = command("network.getData", request=request_id, dataType="response",
                               collector=collector, disown=True)["bytes"]
                if body["type"] == "base64":
                    payload = base64.b64decode(body["value"], validate=True).decode("utf-8")
                elif body["type"] == "string":
                    payload = body["value"]
                else:
                    raise ValueError("Codificación BiDi desconocida")
                media_response = json.loads(payload)
                media = media_response["media"]
                if not isinstance(media, list):
                    raise ValueError("media no es una lista")
            except (WebDriverException, ValueError, KeyError, TypeError):
                warnings.warn(
                    "La respuesta de medias/profile no pudo leerse como JSON; se conservan los datos del HTML.",
                    RuntimeWarning,
                )
                return result
            # Conservar la respuesta que ya obtuvo Firefox, sin repetirla con
            # otro cliente HTTP que puede ser rechazado por Cloudflare.
            result["media_response"] = media_response
            return result
    
