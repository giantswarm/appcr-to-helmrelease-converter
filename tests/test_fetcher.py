from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from fetcher import FetchError, fetch


APP_DICT = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "App",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {
        "name": "my-app",
        "namespace": "monitoring",
        "version": "1.2.3",
        "catalog": "giantswarm",
        "catalogNamespace": "giantswarm",
    },
}

APP_DICT_NO_CATALOG_NS = {
    **APP_DICT,
    "spec": {k: v for k, v in APP_DICT["spec"].items() if k != "catalogNamespace"},
}

CATALOG_DICT = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "Catalog",
    "metadata": {"name": "giantswarm", "namespace": "giantswarm"},
    "spec": {
        "repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/charts/giantswarm"}],
    },
}


def _mock_api(app_dict=APP_DICT, catalog_dict=CATALOG_DICT):
    api = MagicMock()
    api.get_namespaced_custom_object.side_effect = [app_dict, catalog_dict]
    return api


class TestFetch:
    def test_returns_app_and_catalog_dicts(self):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()):
            app, catalog = fetch("my-app", "giantswarm")
        assert app == APP_DICT
        assert catalog == CATALOG_DICT

    def test_passes_context_to_load_kube_config(self):
        with patch("kubernetes.config.load_kube_config") as mock_load, \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()):
            fetch("my-app", "giantswarm", context="my-context")
        mock_load.assert_called_once_with(context="my-context")

    def test_passes_none_context_when_not_specified(self):
        with patch("kubernetes.config.load_kube_config") as mock_load, \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()):
            fetch("my-app", "giantswarm")
        mock_load.assert_called_once_with(context=None)

    def test_invalid_context_raises_fetch_error(self):
        msg = "Invalid kube-config file. Expected object with name asdasd in contexts list"
        with patch("kubernetes.config.load_kube_config", side_effect=ConfigException(msg)):
            with pytest.raises(FetchError, match="Invalid kube-config"):
                fetch("my-app", "giantswarm", context="asdasd")

    def test_invalid_context_error_contains_no_traceback(self):
        msg = "Invalid kube-config file. Expected object with name asdasd in contexts list"
        with patch("kubernetes.config.load_kube_config", side_effect=ConfigException(msg)):
            with pytest.raises(FetchError) as exc_info:
                fetch("my-app", "giantswarm", context="asdasd")
        assert "Traceback" not in str(exc_info.value)

    def test_fetches_catalog_cr_using_spec_catalog_and_catalog_namespace(self):
        mock_api = _mock_api()
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            fetch("my-app", "giantswarm")
        second_call = mock_api.get_namespaced_custom_object.call_args_list[1]
        assert second_call.kwargs == {
            "group": "application.giantswarm.io",
            "version": "v1alpha1",
            "namespace": "giantswarm",
            "plural": "catalogs",
            "name": "giantswarm",
        }

    def test_catalog_found_in_default_when_catalog_namespace_absent(self):
        mock_api = _mock_api(app_dict=APP_DICT_NO_CATALOG_NS)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            _, catalog = fetch("my-app", "giantswarm")
        second_call = mock_api.get_namespaced_custom_object.call_args_list[1]
        assert second_call.kwargs["namespace"] == "default"
        assert catalog == CATALOG_DICT

    def test_catalog_found_in_giantswarm_when_not_in_default(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [
            APP_DICT_NO_CATALOG_NS,
            ApiException(status=404),
            CATALOG_DICT,
        ]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            _, catalog = fetch("my-app", "giantswarm")
        third_call = mock_api.get_namespaced_custom_object.call_args_list[2]
        assert third_call.kwargs["namespace"] == "giantswarm"
        assert catalog == CATALOG_DICT

    def test_raises_fetch_error_when_catalog_not_in_default_or_giantswarm(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [
            APP_DICT_NO_CATALOG_NS,
            ApiException(status=404),
            ApiException(status=404),
        ]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="not found in default or giantswarm"):
                fetch("my-app", "giantswarm")

    def test_raises_fetch_error_immediately_on_non_404_during_fallback(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [
            APP_DICT_NO_CATALOG_NS,
            ApiException(status=403),
        ]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError):
                fetch("my-app", "giantswarm")
        assert mock_api.get_namespaced_custom_object.call_count == 2

    def test_raises_fetch_error_when_catalog_not_found_in_explicit_namespace(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [APP_DICT, ApiException(status=404)]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="not found in giantswarm"):
                fetch("my-app", "giantswarm")

    def test_raises_fetch_error_when_app_cr_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="App giantswarm/my-app"):
                fetch("my-app", "giantswarm")

    def test_app_not_found_error_extracts_kubernetes_message(self):
        e = ApiException(status=404)
        e.body = '{"message": "apps.application.giantswarm.io \\"my-app\\" not found"}'
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = e
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError) as exc_info:
                fetch("my-app", "giantswarm")
        assert 'apps.application.giantswarm.io "my-app" not found' in str(exc_info.value)

    def test_app_not_found_error_contains_no_http_headers(self):
        e = ApiException(status=404)
        e.body = '{"message": "apps.application.giantswarm.io \\"my-app\\" not found"}'
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = e
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError) as exc_info:
                fetch("my-app", "giantswarm")
        assert "HTTP response" not in str(exc_info.value)

    def test_raises_fetch_error_when_catalog_cr_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [APP_DICT, ApiException(status=404)]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="Catalog giantswarm"):
                fetch("my-app", "giantswarm")

    def test_fetches_app_cr_with_correct_coordinates(self):
        mock_api = _mock_api()
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            fetch("my-app", "giantswarm")
        first_call = mock_api.get_namespaced_custom_object.call_args_list[0]
        assert first_call.kwargs == {
            "group": "application.giantswarm.io",
            "version": "v1alpha1",
            "namespace": "giantswarm",
            "plural": "apps",
            "name": "my-app",
        }
