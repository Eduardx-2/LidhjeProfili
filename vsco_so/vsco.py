import json
import sys
import subprocess
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
import requests
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
if __package__:
    from .errors import ErrorShow
    from .user_data import UserSession, ProfileExtractionError, UserId
else:

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from vsco_so.errors import ErrorShow
    from vsco_so.user_data import UserSession, ProfileExtractionError, UserId

from data.image import ImageData
from phone_ident import identify_phone

class SessionDataApi:
    def __init__(self):
        self.END = "https://vsco.co/api/3.0/medias/profile?site_id="
        self.REQ = requests.session()
        self._http2 = None

    def obtening_data_profile(self,driver,uri, cursor="", *, _state=None):
        """Obtiene una página; iter_profile_pages comparte la sesión entre páginas."""
        state = {} if _state is None else _state
        if not state:
            user = UserId(driver=driver, uri=uri)
            state.update(user=user, data=user.extract_info_id_token())
        user, session_data = state["user"], state["data"]
        id_user = str(session_data.get("site_id", ""))
        if not id_user.isdecimal():
            raise ProfileExtractionError("No se obtuvo un site_id válido para el perfil.")
        authorization = session_data["authorization"]
        url = self.END + id_user + "&limit=14&cursor=" + quote(cursor, safe="")

        # Firefox ya puede haber recibido la galería durante la extracción.
        media_response = session_data.pop("media_response", None) if not cursor else None
        if isinstance(media_response, dict) and isinstance(media_response.get("media"), list):
            req = requests.Response()
            req.status_code = 200
            req.url = session_data.get("media_request_url", url)
            req.headers["content-type"] = "application/json"
            req.encoding = "utf-8"
            req._content = json.dumps(media_response).encode("utf-8")
            return req

        if "headers" not in state:
            state["headers"] = {
                "authorization": authorization,
                "content-type": "application/json",
                "priority": "u=1, i",
                "referer": uri,
                "accept": "*/*",
                "accept-language": "es-419",
                "user-agent": driver.execute_script("return navigator.userAgent"),
                "x-client-build": "1",
                "x-client-platform": "web",
                "cookie": (session_data["cookie"] if "cookie" in session_data
                           else user.current_cookie_web()),
            }
        head = state["headers"]
        token_refreshed = cookies_refreshed = False
        while True:
            try:
                transport = state.get("transport", "requests")
                if transport == "http2":
                    try:
                        req = self._http2_request(url, head)
                    except (ImportError, requests.RequestException):
                        state["transport"] = "curl"
                        continue
                elif transport == "curl":
                    req = self._curl_request(url, head)
                else:
                    req = self.REQ.get(url, headers=head.copy(), timeout=30)
                req.raise_for_status()
                return req
            except requests.HTTPError:
                try:
                    error = req.json()
                except ValueError:
                    error = {}
                if not isinstance(error, dict):
                    error = {}

                if error.get("errorType") == "session_expired":
                    if token_refreshed:
                        raise
                    head["authorization"] = user.extract_info_id_token()["authorization"]
                    token_refreshed = True
                elif req.status_code == 401 and not token_refreshed:
                    head["authorization"] = user.extract_info_id_token()["authorization"]
                    token_refreshed = True
                elif req.status_code == 403 and "cloudflare" in req.text.lower():
                    if transport != "curl":
                        state["transport"] = "http2" if transport == "requests" else "curl"
                        continue
                    raise ProfileExtractionError(
                        "Cloudflare bloqueó la petición de curl; "
                        "no se pudo consultar la galería con esta sesión."
                    ) from None
                elif req.status_code in (401, 403) and not cookies_refreshed:
                    head["cookie"] = user.session_cookie_web
                    cookies_refreshed = True
                else:
                    raise

    def close(self):
        self.REQ.close()
        if self._http2 is not None:
            self._http2.close()

    def _http2_request(self, url, headers):
        import httpx
        if self._http2 is None:
            self._http2 = httpx.Client(http2=True, timeout=30)
        try:
            result = self._http2.get(url, headers=headers)
        except httpx.RequestError:
            raise requests.ConnectionError("Falló la consulta HTTP/2 del perfil.") from None
        return self._response(url, result.status_code, result.content)

    @staticmethod
    def _response(url, status, body):
        response = requests.Response()
        response.status_code = status
        response.url = url
        response.encoding = 'utf-8'
        response._content = body
        return response

    @classmethod
    def _curl_request(cls, url, headers):
        if any("\r" in name + value or "\n" in name + value
               for name, value in headers.items()):
            raise ProfileExtractionError("Las cabeceras HTTP contienen caracteres inválidos.")
        try:
            result = subprocess.run(
                ["curl", "--disable", "--silent", "--show-error", "--compressed",
                 "--connect-timeout", "10", "--max-time", "30", "--header", "@-",
                 "--write-out", "\n%{http_code}", "--url", url],
                input="".join(f"{name}: {value}\n" for name, value in headers.items()).encode(),
                capture_output=True, timeout=35,
            )
        except FileNotFoundError:
            raise ProfileExtractionError("Instala curl para consultar el perfil con este transporte.") from None
        except subprocess.TimeoutExpired:
            raise requests.Timeout("La consulta del perfil con curl excedió el tiempo permitido.") from None
        if result.returncode:
            raise requests.ConnectionError(
                f"La consulta del perfil con curl falló (código {result.returncode})."
            )
        try:
            body, status = result.stdout.rsplit(b"\n", 1)
            status_code = int(status)
            if not 100 <= status_code <= 599:
                raise ValueError
        except ValueError:
            raise ProfileExtractionError("curl devolvió una respuesta HTTP inválida.") from None
        return cls._response(url, status_code, body)

    def iter_profile_pages(self, driver, uri):
        
        state = {}
        cursor = ""
        seen = set()
        while True:
            response = self.obtening_data_profile(driver, uri, cursor, _state=state)
            try:
                page = response.json()
            except ValueError:
                raise ProfileExtractionError("La página del perfil no contiene JSON válido.") from None
            finally:
                response.close()
            if not isinstance(page, dict) or not isinstance(page.get("media"), list):
                raise ProfileExtractionError("La página del perfil no contiene una lista media.")
            next_cursor = page.get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ProfileExtractionError("La API devolvió un next_cursor inválido.")
            if next_cursor and next_cursor in seen:
                raise ProfileExtractionError("La API repitió next_cursor; el perfil está incompleto.")
            yield page
            del page, response
            if not next_cursor:
                return
            seen.add(next_cursor)
            cursor = next_cursor

class Vsco:

    def __init__(self, uri: str):
        self._uri = uri

    def _milisegundo(self,tiempoMili) -> str | None:
        if tiempoMili is None:
            return None
        scd_t,mili = divmod(tiempoMili,1000)
        datetimes = datetime.fromtimestamp(scd_t).replace(microsecond=mili * 1000)
        return datetimes.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def extract_info_json(self) -> dict:
        if not self._uri.strip():
            return ErrorShow("La URL del perfil no puede estar vacía.").error_json
        try:
            with closing(self._profile_images()) as images:
                result = self._extract_images(images)
            summary = result.pop()
            return {"images": result, "device_summary": summary}
        except ProfileExtractionError as error:
            return ErrorShow(str(error)).error_json
        except WebDriverException as error:
            return ErrorShow(
                "No se pudo iniciar o utilizar Firefox mediante Selenium.",
                selenium_stderr=str(error),
            ).error_json
        except requests.RequestException:
            return ErrorShow(
                "No se pudo obtener el perfil de VSCO tras solicitarlo a la API."
            ).error_json

    def _profile_images(self):
        api = SessionDataApi()
        try:
            with self._main_webdriver() as driver:
                with closing(api.iter_profile_pages(driver, self._uri)) as pages:
                    for page in pages:
                        for item in page['media']:
                            if item.get('type') == 'image':
                                yield item['image']
                        del page
        finally:
            api.close()

    def _image_info(self, img):
        return {"dataImages": ImageData(
            captura_date=self._milisegundo(img.get('capture_date', img.get('captureDate'))),
            captura_date_mili=self._milisegundo(img.get('capture_date_ms', img.get('captureDateMs'))),
            upload_date=self._milisegundo(img.get('upload_date', img.get('uploadDate'))),
            upload_update=self._milisegundo(img.get('last_updated', img.get('lastUpdated'))),
            uri=img.get('responsive_url', img.get('responsiveUrl')),
        ).createInfoseidd()}

    def _extract_images(self, images, *, bayes=None):
        dataJson = []
        model_phone_image = []
        make = []
        dates = []
        for img in images:
            meta = img.get('image_meta', img.get('imageMeta')) or {}
            # Mantener un par por imagen incluso si falta marca o modelo.
            # El software de edición no se identifica el dispositivo de captura.
            make.append(meta.get('make'))
            model_phone_image.append(meta.get('model'))
            capture_date = img.get('capture_date', img.get('captureDate'))
            capture_date_ms = img.get('capture_date_ms', img.get('captureDateMs'))
            dates.append(capture_date_ms if capture_date_ms is not None else capture_date)
            dataJson.append(self._image_info(img))
        dataJson.append(identify_phone(make, model_phone_image, dates=dates, bayes=bayes)) 
        return dataJson

    @contextmanager
    def _main_webdriver(self):
        scraper = UserSession()
        with webdriver.Firefox(options=scraper.opt) as driver:
            driver.set_page_load_timeout(30) 
            yield driver
