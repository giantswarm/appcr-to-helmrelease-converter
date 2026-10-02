import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi

_MANIFEST_ACCEPT = ", ".join((
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
_TIMEOUT_S = 15


def _ssl_context() -> ssl.SSLContext:
    # Same CA bundle the kubernetes client verifies against.
    return ssl.create_default_context(cafile=certifi.where())


class RegistryError(Exception):
    pass


def _challenge_params(header: str) -> dict:
    return dict(re.findall(r'(\w+)="([^"]*)"', header or ""))


def _anonymous_token(challenge: str) -> str:
    params = _challenge_params(challenge)
    realm = params.pop("realm", None)
    if not realm:
        raise RegistryError(f"registry sent an unusable auth challenge: {challenge!r}")
    url = f"{realm}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S, context=_ssl_context()) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, ValueError) as e:
        raise RegistryError(f"failed to get an anonymous registry token from {realm}: {e}") from e
    token = body.get("token") or body.get("access_token")
    if not token:
        raise RegistryError(f"registry token response from {realm} carries no token")
    return token


class _Unauthorized(Exception):
    def __init__(self, challenge: str):
        self.challenge = challenge


def _manifest_exists(url: str, ref: str, token: str | None) -> bool:
    headers = {"Accept": _MANIFEST_ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, method="HEAD", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S, context=_ssl_context()):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        if e.code == 401 and token is None:
            raise _Unauthorized(e.headers.get("WWW-Authenticate", "")) from e
        raise RegistryError(f"failed to look up {ref}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RegistryError(f"failed to look up {ref}: {e.reason}") from e


def oci_tag_exists(host: str, repository: str, tag: str) -> bool:
    url = f"https://{host}/v2/{repository}/manifests/{tag}"
    ref = f"{host}/{repository}:{tag}"
    try:
        return _manifest_exists(url, ref, None)
    except _Unauthorized as e:
        token = _anonymous_token(e.challenge)
    return _manifest_exists(url, ref, token)
