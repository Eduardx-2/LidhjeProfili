"""Consulta el perfil usando VSCO_TOKEN y VSCO_COOKIE del entorno."""

import os
import sys

import requests


def consultar_perfil():
    token = os.environ.get("VSCO_TOKEN", "").strip()
    cookie = os.environ.get("VSCO_COOKIE", "").strip()
    if not token or not cookie:
        raise ValueError("Define VSCO_TOKEN y VSCO_COOKIE antes de ejecutar el script.")

    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "es-419",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "cookie": cookie,
        "if-modified-since": "Tue, 17 Nov 2026 00:56:39 GMT",
        "priority": "u=1, i",
        "referer": "https://vsco.co/eussitaaa/gallery",
        "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version-list": '"Not-A.Brand";v="99.0.0.0", "Brave";v="157.0.0.0", "Chromium";v="157.0.0.0"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Linux"',
        "sec-ch-ua-platform-version": '""',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
        "x-client-build": "1",
        "x-client-platform": "web",
    }
    return requests.get(
        "https://vsco.co/api/3.0/medias/profile",
        params={"site_id": "299352875", "limit": "14", "cursor": ""},
        headers=headers,
        timeout=20,
    )


if __name__ == "__main__":
    try:
        response = consultar_perfil()
        print(f"HTTP {response.status_code}")
        if response.status_code == 304:
            print("Sin cambios desde la fecha indicada; la respuesta no contiene cuerpo.")
        else:
            print(response.text)
        response.raise_for_status()
    except (ValueError, requests.RequestException) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
