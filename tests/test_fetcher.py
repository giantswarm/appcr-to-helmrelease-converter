from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from fetcher import FetchError, FetchResult, fetch


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


def _mock_core_api(resources=None):
    core_api = MagicMock()
    resources = resources or {}
    def _read_cm(name, namespace):
        key = ("ConfigMap", name, namespace)
        r = resources.get(key)
        if r is None:
            from kubernetes.client.exceptions import ApiException
            raise ApiException(status=404)
        mock_r = MagicMock()
        mock_r.to_dict.return_value = r
        return mock_r
    def _read_secret(name, namespace):
        key = ("Secret", name, namespace)
        r = resources.get(key)
        if r is None:
            from kubernetes.client.exceptions import ApiException
            raise ApiException(status=404)
        mock_r = MagicMock()
        mock_r.to_dict.return_value = r
        return mock_r
    core_api.read_namespaced_config_map.side_effect = _read_cm
    core_api.read_namespaced_secret.side_effect = _read_secret
    return core_api


_CM = {"data": {"configmap-values.yaml": "foo: bar"}}
_SECRET = {"data": {"secret-values.yaml": "cGFzcw=="}}


class TestFetch:
    def test_returns_fetch_result(self):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert isinstance(result, FetchResult)
        assert result.app == APP_DICT
        assert result.catalog == CATALOG_DICT

    def test_returns_app_and_catalog_dicts(self):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.app == APP_DICT
        assert result.catalog == CATALOG_DICT

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
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        second_call = mock_api.get_namespaced_custom_object.call_args_list[1]
        assert second_call.kwargs["namespace"] == "default"
        assert result.catalog == CATALOG_DICT

    def test_catalog_found_in_giantswarm_when_not_in_default(self):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = [
            APP_DICT_NO_CATALOG_NS,
            ApiException(status=404),
            CATALOG_DICT,
        ]
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        third_call = mock_api.get_namespaced_custom_object.call_args_list[2]
        assert third_call.kwargs["namespace"] == "giantswarm"
        assert result.catalog == CATALOG_DICT

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

class TestFetchReferencedConfigs:
    _NS = "giantswarm"

    def _fetch_with_app(self, spec_extra):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"], **spec_extra}}
        custom_api = MagicMock()
        custom_api.get_namespaced_custom_object.side_effect = [app, CATALOG_DICT]
        cm_key = ("ConfigMap", "my-cm", self._NS)
        secret_key = ("Secret", "my-secret", self._NS)
        resources = {cm_key: _CM, secret_key: _SECRET}
        core_api = _mock_core_api(resources)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=custom_api), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            return fetch("my-app", self._NS)

    def test_config_configmap_fetched_into_referenced_configs(self):
        result = self._fetch_with_app({"config": {"configMap": {"name": "my-cm", "namespace": self._NS}}})
        assert ("ConfigMap", "my-cm", self._NS) in result.referenced_configs
        assert result.referenced_configs[("ConfigMap", "my-cm", self._NS)] == _CM

    def test_config_secret_fetched_into_referenced_configs(self):
        result = self._fetch_with_app({"config": {"secret": {"name": "my-secret", "namespace": self._NS}}})
        assert ("Secret", "my-secret", self._NS) in result.referenced_configs
        assert result.referenced_configs[("Secret", "my-secret", self._NS)] == _SECRET

    def test_absent_namespace_uses_app_namespace(self):
        result = self._fetch_with_app({"config": {"configMap": {"name": "my-cm"}}})
        assert ("ConfigMap", "my-cm", self._NS) in result.referenced_configs

    def test_404_stored_as_none(self):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"],
               "config": {"configMap": {"name": "missing-cm", "namespace": self._NS}}}}
        custom_api = MagicMock()
        custom_api.get_namespaced_custom_object.side_effect = [app, CATALOG_DICT]
        core_api = _mock_core_api({})  # nothing found → 404
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=custom_api), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            result = fetch("my-app", self._NS)
        assert result.referenced_configs[("ConfigMap", "missing-cm", self._NS)] is None

    def test_no_refs_returns_empty_referenced_configs(self):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.referenced_configs == {}

    def test_extra_configs_entry_fetched_into_referenced_configs(self):
        result = self._fetch_with_app({"extraConfigs": [{"name": "my-cm", "namespace": self._NS}]})
        assert ("ConfigMap", "my-cm", self._NS) in result.referenced_configs

    def test_same_ref_in_config_and_extra_configs_fetched_once(self):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"],
               "config": {"configMap": {"name": "my-cm", "namespace": self._NS}},
               "extraConfigs": [{"name": "my-cm", "namespace": self._NS}]}}
        custom_api = MagicMock()
        custom_api.get_namespaced_custom_object.side_effect = [app, CATALOG_DICT]
        core_api = _mock_core_api({("ConfigMap", "my-cm", self._NS): _CM})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=custom_api), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            fetch("my-app", self._NS)
        assert core_api.read_namespaced_config_map.call_count == 1

    def test_extra_configs_entry_without_name_skipped(self):
        result = self._fetch_with_app({"extraConfigs": [{"kind": "ConfigMap"}]})
        assert result.referenced_configs == {}

    def test_same_cm_in_config_and_user_config_fetched_once(self):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"],
               "config": {"configMap": {"name": "my-cm", "namespace": self._NS}},
               "userConfig": {"configMap": {"name": "my-cm", "namespace": self._NS}}}}
        custom_api = MagicMock()
        custom_api.get_namespaced_custom_object.side_effect = [app, CATALOG_DICT]
        core_api = _mock_core_api({("ConfigMap", "my-cm", self._NS): _CM})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=custom_api), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            fetch("my-app", self._NS)
        assert core_api.read_namespaced_config_map.call_count == 1

    def test_non_404_api_exception_on_config_ref_raises_fetch_error(self):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"],
               "config": {"configMap": {"name": "my-cm", "namespace": self._NS}}}}
        custom_api = MagicMock()
        custom_api.get_namespaced_custom_object.side_effect = [app, CATALOG_DICT]
        core_api = MagicMock()
        core_api.read_namespaced_config_map.side_effect = ApiException(status=403)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=custom_api), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            with pytest.raises(FetchError):
                fetch("my-app", self._NS)


class TestFetchAppCrCoordinates:
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
