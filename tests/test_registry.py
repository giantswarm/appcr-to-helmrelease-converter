import io
import json
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from fetcher.registry import RegistryError, oci_tag_exists

_CHALLENGE = (
    'Bearer realm="https://gsoci.azurecr.io/oauth2/token",service="gsoci.azurecr.io",'
    'scope="repository:charts/giantswarm/release-aws:pull"'
)


def _http_error(code, challenge=None):
    headers = Message()
    if challenge is not None:
        headers["WWW-Authenticate"] = challenge
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO())


def _ok(body=None):
    resp = MagicMock()
    resp.__enter__.return_value = io.BytesIO(json.dumps(body or {}).encode())
    return resp


def _lookup(*responses):
    with patch("urllib.request.urlopen", side_effect=list(responses)) as urlopen:
        result = oci_tag_exists("gsoci.azurecr.io", "charts/giantswarm/release-aws", "34.0.0")
    return result, urlopen


class TestOciTagExists:
    def test_anonymous_head_found(self):
        result, urlopen = _lookup(_ok())
        assert result is True
        request = urlopen.call_args_list[0].args[0]
        assert request.full_url == "https://gsoci.azurecr.io/v2/charts/giantswarm/release-aws/manifests/34.0.0"
        assert request.get_method() == "HEAD"

    def test_anonymous_head_not_found(self):
        result, _ = _lookup(_http_error(404))
        assert result is False

    def test_token_flow_found(self):
        result, urlopen = _lookup(_http_error(401, _CHALLENGE), _ok({"access_token": "tok"}), _ok())
        assert result is True
        token_url = urlopen.call_args_list[1].args[0]
        assert token_url.startswith("https://gsoci.azurecr.io/oauth2/token?")
        assert "service=gsoci.azurecr.io" in token_url
        assert "scope=repository%3Acharts%2Fgiantswarm%2Frelease-aws%3Apull" in token_url
        assert "realm" not in token_url
        retry = urlopen.call_args_list[2].args[0]
        assert retry.get_header("Authorization") == "Bearer tok"

    def test_token_flow_not_found(self):
        result, _ = _lookup(_http_error(401, _CHALLENGE), _ok({"token": "tok"}), _http_error(404))
        assert result is False

    def test_rejected_token_is_error(self):
        with pytest.raises(RegistryError, match="HTTP 401"):
            _lookup(_http_error(401, _CHALLENGE), _ok({"token": "tok"}), _http_error(401, _CHALLENGE))

    def test_server_error_is_error(self):
        with pytest.raises(RegistryError, match="HTTP 500"):
            _lookup(_http_error(500))

    def test_network_error_is_error(self):
        with pytest.raises(RegistryError, match="connection refused"):
            _lookup(urllib.error.URLError("connection refused"))

    def test_challenge_without_realm_is_error(self):
        with pytest.raises(RegistryError, match="unusable auth challenge"):
            _lookup(_http_error(401, 'Bearer service="x"'))

    def test_missing_challenge_header_is_error(self):
        with pytest.raises(RegistryError, match="unusable auth challenge"):
            _lookup(_http_error(401))

    def test_token_endpoint_unreachable_is_error(self):
        with pytest.raises(RegistryError, match="anonymous registry token"):
            _lookup(_http_error(401, _CHALLENGE), urllib.error.URLError("boom"))

    def test_token_response_not_json_is_error(self):
        resp = MagicMock()
        resp.__enter__.return_value = io.BytesIO(b"not json")
        with pytest.raises(RegistryError, match="anonymous registry token"):
            _lookup(_http_error(401, _CHALLENGE), resp)

    def test_token_response_without_token_is_error(self):
        with pytest.raises(RegistryError, match="carries no token"):
            _lookup(_http_error(401, _CHALLENGE), _ok({}))

    def test_verifies_tls_with_certifi_bundle(self):
        with patch("certifi.where", return_value="/bundle.pem") as where, \
             patch("ssl.create_default_context") as ctx:
            _lookup(_ok())
        where.assert_called()
        ctx.assert_called_with(cafile="/bundle.pem")
