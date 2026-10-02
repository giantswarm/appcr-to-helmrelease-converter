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


_HR_DICT = {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
            "metadata": {"name": "coredns", "namespace": "giantswarm"}}


def _mock_api(app_dict=APP_DICT, catalog_dict=CATALOG_DICT, helm_releases=None):
    api = MagicMock()
    hrs = helm_releases or {}

    def _get(group, version, namespace, plural, name):
        if plural == "apps":
            return app_dict
        if plural == "catalogs":
            return catalog_dict
        if plural == "helmreleases":
            hr = hrs.get(name)
            if hr is None:
                raise ApiException(status=404)
            return hr
        raise ApiException(status=404)

    api.get_namespaced_custom_object.side_effect = _get
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
        def _get(group, version, namespace, plural, name):
            if plural == "apps":
                return APP_DICT_NO_CATALOG_NS
            if plural == "catalogs" and namespace == "default":
                raise ApiException(status=404)
            if plural == "catalogs" and namespace == "giantswarm":
                return CATALOG_DICT
            raise ApiException(status=404)  # helmreleases (no depends-on)

        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = _get
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        catalog_calls = [c for c in mock_api.get_namespaced_custom_object.call_args_list
                         if c.kwargs.get("plural") == "catalogs"]
        assert catalog_calls[0].kwargs["namespace"] == "default"
        assert catalog_calls[1].kwargs["namespace"] == "giantswarm"
        assert result.catalog == CATALOG_DICT

    def test_raises_fetch_error_when_catalog_not_in_default_or_giantswarm(self):
        def _get(group, version, namespace, plural, name):
            if plural == "apps":
                return APP_DICT_NO_CATALOG_NS
            raise ApiException(status=404)

        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.side_effect = _get
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
        cm_key = ("ConfigMap", "my-cm", self._NS)
        secret_key = ("Secret", "my-secret", self._NS)
        resources = {cm_key: _CM, secret_key: _SECRET}
        core_api = _mock_core_api(resources)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api(app_dict=app)), \
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
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api(app_dict=app)), \
             patch("kubernetes.client.CoreV1Api", return_value=_mock_core_api({})):
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
        core_api = _mock_core_api({("ConfigMap", "my-cm", self._NS): _CM})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api(app_dict=app)), \
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
        core_api = _mock_core_api({("ConfigMap", "my-cm", self._NS): _CM})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api(app_dict=app)), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            fetch("my-app", self._NS)
        assert core_api.read_namespaced_config_map.call_count == 1

    def test_non_404_api_exception_on_config_ref_raises_fetch_error(self):
        app = {**APP_DICT, "spec": {**APP_DICT["spec"],
               "config": {"configMap": {"name": "my-cm", "namespace": self._NS}}}}
        core_api = MagicMock()
        core_api.read_namespaced_config_map.side_effect = ApiException(status=403)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api(app_dict=app)), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            with pytest.raises(FetchError):
                fetch("my-app", self._NS)


_APP_WITH_DEPS = {
    **APP_DICT,
    "metadata": {**APP_DICT["metadata"], "annotations": {"app-operator.giantswarm.io/depends-on": "coredns"}},
}


class TestFetchDependencyHelmReleases:
    def test_no_depends_on_annotation_yields_empty_dict(self):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.dependency_helm_releases == {}

    def test_dep_found_included_in_result(self):
        api = _mock_api(app_dict=_APP_WITH_DEPS, helm_releases={"coredns": _HR_DICT})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.dependency_helm_releases == {"coredns": _HR_DICT}
        hr_calls = [c for c in api.get_namespaced_custom_object.call_args_list
                    if c.kwargs.get("plural") == "helmreleases"]
        assert hr_calls[0].kwargs["namespace"] == "giantswarm"

    def test_dep_not_found_stored_as_none(self):
        api = _mock_api(app_dict=_APP_WITH_DEPS, helm_releases={})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.dependency_helm_releases == {"coredns": None}

    def test_null_annotations_yields_empty_dict(self):
        app = {**_APP_WITH_DEPS, "metadata": {**_APP_WITH_DEPS["metadata"], "annotations": None}}
        api = _mock_api(app_dict=app)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"):
            result = fetch("my-app", "giantswarm")
        assert result.dependency_helm_releases == {}

    def test_non_404_error_on_dep_lookup_raises_fetch_error(self):
        api = MagicMock()

        def _get(group, version, namespace, plural, name):
            if plural == "apps":
                return _APP_WITH_DEPS
            if plural == "catalogs":
                return CATALOG_DICT
            raise ApiException(status=403)

        api.get_namespaced_custom_object.side_effect = _get
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"):
            with pytest.raises(FetchError):
                fetch("my-app", "giantswarm")


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


class TestFetchPullSecret:
    _NS = "giantswarm"
    _PULL_SECRET = {"type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": "e30="}}

    def _fetch(self, pull_secret, resources=None):
        core_api = _mock_core_api(resources if resources is not None else
                                  {("Secret", "regcred", self._NS): self._PULL_SECRET})
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api):
            return fetch("my-app", self._NS, None, pull_secret), core_api

    def test_pull_secret_is_none_when_flag_absent(self):
        result, _ = self._fetch(None)
        assert result.pull_secret is None

    def test_no_secret_read_when_flag_absent(self):
        _, core_api = self._fetch(None)
        assert core_api.read_namespaced_secret.call_count == 0

    def test_pull_secret_fetched_from_app_namespace(self):
        result, _ = self._fetch("regcred")
        assert result.pull_secret == self._PULL_SECRET

    def test_pull_secret_read_with_app_namespace(self):
        _, core_api = self._fetch("regcred")
        core_api.read_namespaced_secret.assert_called_once_with(name="regcred", namespace=self._NS)

    def test_missing_pull_secret_is_none_not_error(self):
        result, _ = self._fetch("regcred", resources={})
        assert result.pull_secret is None

    def test_non_404_pull_secret_error_raises_fetch_error(self):
        core_api = MagicMock()
        core_api.read_namespaced_secret.side_effect = ApiException(status=403)
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=_mock_api()), \
             patch("kubernetes.client.CoreV1Api", return_value=core_api), \
             pytest.raises(FetchError):
            fetch("my-app", self._NS, None, "regcred")


_CLUSTER_APP = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "App",
    "metadata": {"name": "mycluster", "namespace": "org-acme"},
    "spec": {
        "name": "cluster-aws",
        "namespace": "org-acme",
        "version": "7.2.5",
        "catalog": "cluster",
        "catalogNamespace": "giantswarm",
        "kubeConfig": {"inCluster": True},
    },
}

_CAPI_CLUSTER = {
    "metadata": {"name": "mycluster", "labels": {"release.giantswarm.io/version": "34.0.0"}},
}

_RELEASE_CR = {"metadata": {"name": "aws-34.0.0"},
               "spec": {"components": [{"name": "cluster-aws", "version": "7.2.5"}]}}


class TestFetchReleaseChartFacts:
    def _api(self, clusters=None, list_errors=(), release_cr=_RELEASE_CR, release_error=None):
        api = _mock_api(app_dict=_CLUSTER_APP)
        errors = list(list_errors)

        def _list(group, version, namespace, plural, label_selector):
            if errors:
                raise errors.pop(0)
            return {"items": [_CAPI_CLUSTER] if clusters is None else clusters}

        def _get_cluster(group, version, plural, name):
            if release_error is not None:
                raise release_error
            if release_cr is None:
                raise ApiException(status=404)
            return release_cr

        api.list_namespaced_custom_object.side_effect = _list
        api.get_cluster_custom_object.side_effect = _get_cluster
        return api

    def _fetch(self, api, published=True):
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"), \
             patch("fetcher.oci_tag_exists", return_value=published) as tag_exists:
            result = fetch("mycluster", "org-acme")
        return result, tag_exists

    def test_not_fetched_for_ordinary_app(self):
        api = _mock_api()
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api"), \
             patch("fetcher.oci_tag_exists") as tag_exists:
            result = fetch("my-app", "giantswarm")
        assert result.release_chart is None
        api.list_namespaced_custom_object.assert_not_called()
        tag_exists.assert_not_called()

    def test_full_facts(self):
        api = self._api()
        result, tag_exists = self._fetch(api)
        facts = result.release_chart
        assert facts.provider == "aws"
        assert facts.clusters == [_CAPI_CLUSTER]
        assert facts.release_version == "34.0.0"
        assert facts.release_cr == _RELEASE_CR
        assert facts.published is True
        tag_exists.assert_called_once_with("gsoci.azurecr.io", "charts/giantswarm/release-aws", "34.0.0")

    def test_lists_clusters_by_helm_instance_label_in_target_namespace(self):
        api = self._api()
        self._fetch(api)
        kwargs = api.list_namespaced_custom_object.call_args.kwargs
        assert kwargs["group"] == "cluster.x-k8s.io"
        assert kwargs["version"] == "v1beta2"
        assert kwargs["namespace"] == "org-acme"
        assert kwargs["plural"] == "clusters"
        assert kwargs["label_selector"] == "app.kubernetes.io/instance=mycluster"

    def test_gets_release_cr_by_provider_and_version(self):
        api = self._api()
        self._fetch(api)
        kwargs = api.get_cluster_custom_object.call_args.kwargs
        assert kwargs == {"group": "release.giantswarm.io", "version": "v1alpha1",
                          "plural": "releases", "name": "aws-34.0.0"}

    def test_falls_back_to_v1beta1(self):
        api = self._api(list_errors=[ApiException(status=404)])
        result, _ = self._fetch(api)
        assert api.list_namespaced_custom_object.call_args.kwargs["version"] == "v1beta1"
        assert result.release_chart.release_version == "34.0.0"

    def test_no_served_cluster_version_is_fetch_error(self):
        api = self._api(list_errors=[ApiException(status=404), ApiException(status=404)])
        with pytest.raises(FetchError, match="is not served"):
            self._fetch(api)

    def test_cluster_list_error_is_fetch_error(self):
        api = self._api(list_errors=[ApiException(status=403, reason="Forbidden")])
        with pytest.raises(FetchError, match="failed to list Clusters in org-acme: Forbidden"):
            self._fetch(api)

    def test_no_cluster_stops_early(self):
        api = self._api(clusters=[])
        result, tag_exists = self._fetch(api)
        assert result.release_chart.clusters == []
        assert result.release_chart.release_version is None
        api.get_cluster_custom_object.assert_not_called()
        tag_exists.assert_not_called()

    def test_several_clusters_stop_early(self):
        api = self._api(clusters=[_CAPI_CLUSTER, _CAPI_CLUSTER])
        result, _ = self._fetch(api)
        assert result.release_chart.release_version is None
        api.get_cluster_custom_object.assert_not_called()

    def test_missing_label_stops_early(self):
        api = self._api(clusters=[{"metadata": {"name": "mycluster", "labels": {}}}])
        result, _ = self._fetch(api)
        assert result.release_chart.release_version is None
        api.get_cluster_custom_object.assert_not_called()

    def test_missing_release_cr_stops_early(self):
        api = self._api(release_cr=None)
        result, tag_exists = self._fetch(api)
        assert result.release_chart.release_version == "34.0.0"
        assert result.release_chart.release_cr is None
        tag_exists.assert_not_called()

    def test_release_cr_error_is_fetch_error(self):
        api = self._api(release_error=ApiException(status=500, reason="Internal"))
        with pytest.raises(FetchError, match="failed to fetch Release aws-34.0.0"):
            self._fetch(api)

    def test_unpublished_release_chart(self):
        result, _ = self._fetch(self._api(), published=False)
        assert result.release_chart.published is False

    def test_registry_error_is_fetch_error(self):
        from fetcher.registry import RegistryError
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=self._api()), \
             patch("kubernetes.client.CoreV1Api"), \
             patch("fetcher.oci_tag_exists", side_effect=RegistryError("registry down")):
            with pytest.raises(FetchError, match="registry down"):
                fetch("mycluster", "org-acme")


class TestFetchInstallationValuesLocalized:
    _ENTRY = {"kind": "configMap", "name": "cluster-app-installation-values", "namespace": "giantswarm"}

    def _fetch(self, app):
        api = _mock_api(app_dict=app)
        api.list_namespaced_custom_object.return_value = {"items": []}
        core = _mock_core_api({
            ("ConfigMap", "cluster-app-installation-values", "org-acme"): {"data": {"values.yaml": "a: b"}},
            ("ConfigMap", "cluster-app-installation-values", "giantswarm"): {"data": {"values.yaml": "a: b"}},
        })
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=api), \
             patch("kubernetes.client.CoreV1Api", return_value=core), \
             patch("fetcher.oci_tag_exists"):
            return fetch(app["metadata"]["name"], app["metadata"]["namespace"])

    def test_cluster_chart_app_reads_org_copy(self):
        app = {**_CLUSTER_APP, "spec": {**_CLUSTER_APP["spec"], "extraConfigs": [self._ENTRY]}}
        result = self._fetch(app)
        assert list(result.referenced_configs) == [("ConfigMap", "cluster-app-installation-values", "org-acme")]
        assert result.app == app

    def test_ordinary_app_keeps_giantswarm_reference(self):
        app = {**APP_DICT, "metadata": {"name": "my-app", "namespace": "org-acme"},
               "spec": {**APP_DICT["spec"], "extraConfigs": [self._ENTRY]}}
        result = self._fetch(app)
        assert list(result.referenced_configs) == [("ConfigMap", "cluster-app-installation-values", "giantswarm")]
