from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

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

    def test_catalog_namespace_defaults_to_default_when_absent(self):
        app_without_ns = {**APP_DICT, "spec": {**APP_DICT["spec"]}}
        del app_without_ns["spec"]["catalogNamespace"]
        mock_api = _mock_api(app_dict=app_without_ns)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            fetch("my-app", "giantswarm")
        second_call = mock_api.get_namespaced_custom_object.call_args_list[1]
        assert second_call.kwargs["namespace"] == "default"

    def test_raises_fetch_error_when_app_cr_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="App giantswarm/my-app"):
                fetch("my-app", "giantswarm")

    def test_raises_fetch_error_when_catalog_cr_not_found(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [APP_DICT, ApiException(status=404)]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            with pytest.raises(FetchError, match="Catalog giantswarm/giantswarm"):
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
