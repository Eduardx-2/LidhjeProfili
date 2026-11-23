# Corrección del error de conexión de `vsco.py`

Fecha de revisión: 18 de noviembre de 2026.

## Error capturado

Desde la raíz del proyecto, se ejecutó el código original con:

```bash
python3 vsco_so/vsco.py
```

La ejecución con Firefox real falló con esta cadena de excepciones
(extracto; se omiten los identificadores de sesión y el puerto variable):

```text
ConnectionRefusedError: [Errno 111] Connection refused
urllib3.exceptions.NewConnectionError: ... Failed to establish a new connection ...
urllib3.exceptions.MaxRetryError: HTTPConnectionPool(host='localhost', ...):
Max retries exceeded with url: /session/.../url
```

La llamada que desencadenaba el fallo era:

```python
UserId(s.mainWebidrive(), s.URI).idUser
```

El recorrido era `UserId.idUser` → `UserSession.req_profile` →
`driver.get(profile_url)`. La conexión rechazada correspondía al controlador
local de Selenium.

## Causa

`mainWebidrive()` devolvía el controlador desde dentro de un administrador de contexto:

```python
with webdriver.Firefox(options=scraper.opt) as driver:
    driver.set_page_load_timeout(30)
    return driver
```

Antes de completar el `return`, Python ejecuta `__exit__` del bloque `with`.
En Selenium, ese método llama a `quit()`. Por tanto, `UserId` recibía un
controlador cuya sesión y servicio ya estaban cerrados.

Además, el `except ProfileExtractionError` original rodeaba únicamente la
creación del navegador: la extracción del perfil ocurría después, fuera de
ese bloque, y sus errores no quedaban capturados allí.

## Solución aplicada

Se convirtió `mainWebidrive()` en un administrador de contexto con
`@contextmanager` y `yield driver`. La función `main()` realiza la extracción
dentro de ese contexto:

```python
with s.mainWebidrive() as driver:
    UserId(driver, s.URI).idUser
```

Firefox permanece abierto durante la consulta y se cierra al terminar,
tanto en caso de éxito como si se produce una excepción. También se cierra
si falla la configuración del tiempo de espera.

`main()` captura `ProfileExtractionError` y `WebDriverException`, escribe
el diagnóstico en la salida de errores y devuelve código 1. En caso de
éxito devuelve 0. Las excepciones inesperadas siguen propagándose después
de liberar el navegador, para conservar su diagnóstico.

**Cambio de uso:** cualquier consumidor de `mainWebidrive()` debe utilizar
`with`; ya no debe asignar su resultado directamente como un WebDriver.

También se corrigieron estas importaciones en `vsco_so/vsco.py`:

- `BeautifulSoup`, utilizada por `session_scrap`, no estaba importada.
- `identify_phone`, utilizada por `extract_info_json`, no estaba importada.
- La importación absoluta de `user_data` fallaba al cargar
  `from vsco_so.vsco import Vsco`. Ahora se admite la importación como módulo
  y la ejecución directa del archivo, resolviendo la raíz desde `__file__`.

## Verificación

Entorno utilizado: Python 3.14 y Selenium 4.46.0 instalado en el equipo.

```bash
python3 -m unittest test_vsco_so phone_ident.test_phone_ident -v
```

Resultado: **19 pruebas aprobadas** (8 nuevas y 11 existentes).
Las nuevas pruebas en `test_vsco_so.py` comprueban la extracción antes del
cierre, la liberación del navegador ante errores, la notificación de errores
de inicio y la disponibilidad de las importaciones corregidas. Utilizan
un navegador simulado y no necesitan acceso a Internet.

También se volvió a ejecutar el punto de entrada corregido con Firefox
real, capturando su salida en memoria para no publicar los datos de sesión:

```text
Exit code: 0
Profile output captured: True
```

La primera ejecución dentro del sandbox falló antes de iniciar Firefox con
`RuntimeError: Can't find free port (Unable to bind to IPv4 or IPv6)`.
Esa restricción del entorno se resolvió ejecutando la comprobación con
permiso fuera del sandbox; es independiente del error de ciclo de vida.

## Limitación de la suite antigua

Se ejecutó también:

```bash
python3 -m unittest discover -v
```

Resultado: 19 pruebas aprobadas y un error al importar `test_vsco.py`:

```text
ImportError: cannot import name 'Vsco' from 'vsco' (unknown location)
```

Este fallo ya existía antes de la corrección. Esa suite importa
`from vsco import Vsco` y espera métodos y un formato de respuesta de una
versión anterior, por lo que cambiar solamente su importación no basta
para adaptarla. No se modificó esa suite ni el contrato de extracción de
imágenes en esta corrección del ciclo de vida de Firefox.
