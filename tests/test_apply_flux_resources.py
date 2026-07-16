from unittest.mock import MagicMock, patch

import pytest

from migrator import ApplyFluxResources, MigratorError
from kubernetes.client.exceptions import ApiException


_OCI_REPO = {
    "apiVersion": "source.toolkit.fluxcd.io/v1",
    "kind": "OCIRepository",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {
        "interval": "10m",
        "provider": "generic",
        "ref": {"tag": "1.2.3"},
        "url": "oci://gsoci.azurecr.io/charts/my-app",
    },
}

_HELM_RELEASE = {
    "apiVersion": "helm.toolkit.fluxcd.io/v2",
    "kind": "HelmRelease",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {
        "interval": "5m",
        "releaseName": "my-app",
        "targetNamespace": "giantswarm",
        "chartRef": {"kind": "OCIRepository", "name": "my-app", "namespace": "giantswarm"},
    },
}

_DOCS_OCI = [_OCI_REPO, _HELM_RELEASE]


class TestApplyFluxResources:
    def test_apply_server_side_applies_source_resource(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            step = ApplyFluxResources(api, _DOCS_OCI)
            step.apply()
            req_call = mock_dyn.request.call_args_list[0]
            assert req_call.args[0] == "patch"
            assert "source.toolkit.fluxcd.io" in req_call.args[1]
            assert "v1" in req_call.args[1]
            assert "ocirepositories" in req_call.args[1]
            assert "giantswarm" in req_call.args[1]
            assert req_call.kwargs["field_manager"] == "appcr-to-helmrelease-converter"
            assert req_call.kwargs["force_conflicts"] is True
            assert req_call.kwargs["content_type"] == "application/apply-patch+yaml"

    def test_apply_server_side_applies_helm_release(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            step = ApplyFluxResources(api, _DOCS_OCI)
            step.apply()
            req_call = mock_dyn.request.call_args_list[1]
            assert req_call.args[0] == "patch"
            assert "helm.toolkit.fluxcd.io" in req_call.args[1]
            assert "v2" in req_call.args[1]
            assert "helmreleases" in req_call.args[1]
            assert req_call.kwargs["field_manager"] == "appcr-to-helmrelease-converter"
            assert req_call.kwargs["force_conflicts"] is True
            assert req_call.kwargs["content_type"] == "application/apply-patch+yaml"

    def test_apply_applies_source_before_helm_release(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            step = ApplyFluxResources(api, _DOCS_OCI)
            step.apply()
            req_calls = mock_dyn.request.call_args_list
            assert "ocirepositories" in req_calls[0].args[1]
            assert "helmreleases" in req_calls[1].args[1]

    def test_apply_fails_fast_when_source_apply_raises(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            mock_dyn.request.side_effect = ApiException(status=403)
            step = ApplyFluxResources(api, _DOCS_OCI)
            with pytest.raises(MigratorError, match="failed to apply OCIRepository"):
                step.apply()
            assert mock_dyn.request.call_count == 1

    def test_apply_raises_migrator_error_when_helm_release_apply_fails(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            mock_dyn.request.side_effect = [None, ApiException(status=500)]
            step = ApplyFluxResources(api, _DOCS_OCI)
            with pytest.raises(MigratorError, match="failed to apply HelmRelease"):
                step.apply()

    def test_apply_deletes_source_when_helm_release_apply_fails(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            mock_dyn.request.side_effect = [None, ApiException(status=500)]
            step = ApplyFluxResources(api, _DOCS_OCI)
            with pytest.raises(MigratorError):
                step.apply()
            delete_call = api.delete_namespaced_custom_object.call_args
            assert delete_call.kwargs["plural"] == "ocirepositories"
            assert delete_call.kwargs["name"] == "my-app"

    def test_apply_still_raises_when_source_cleanup_also_fails(self):
        api = MagicMock()
        with patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn = mock_dyn_cls.return_value
            mock_dyn.request.side_effect = [None, ApiException(status=500)]
            api.delete_namespaced_custom_object.side_effect = ApiException(status=500)
            step = ApplyFluxResources(api, _DOCS_OCI)
            with pytest.raises(MigratorError, match="failed to apply HelmRelease"):
                step.apply()

    def test_description_for_oci_path(self):
        step = ApplyFluxResources(MagicMock(), _DOCS_OCI)
        assert step.description == "Apply OCIRepository and HelmRelease"

    def test_description_for_helm_path(self):
        helm_repo = {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "HelmRepository",
            "metadata": {"name": "my-app", "namespace": "giantswarm"},
            "spec": {"interval": "10m", "url": "https://charts.example.io"},
        }
        docs_helm = [helm_repo, _HELM_RELEASE]
        step = ApplyFluxResources(MagicMock(), docs_helm)
        assert step.description == "Apply HelmRepository and HelmRelease"

    def test_revert_suspends_helm_release_before_deleting(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        step = ApplyFluxResources(api, _DOCS_OCI)
        step.revert()
        suspend_call = api.patch_namespaced_custom_object.call_args
        assert suspend_call.kwargs["body"]["spec"]["suspend"] is True
        assert api.patch_namespaced_custom_object.call_count == 1
        delete_call = api.delete_namespaced_custom_object.call_args_list[0]
        assert delete_call.kwargs["plural"] == "helmreleases"

    def test_revert_suspend_uses_merge_patch_not_ssa(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        step = ApplyFluxResources(api, _DOCS_OCI)
        step.revert()
        suspend_call = api.patch_namespaced_custom_object.call_args
        assert "force" not in suspend_call.kwargs
        assert "force_conflicts" not in suspend_call.kwargs

    def test_revert_polls_until_helm_release_gone_then_deletes_source(self):
        api = MagicMock()
        # GET returns the resource twice, then 404
        api.get_namespaced_custom_object.side_effect = [
            {},
            {},
            ApiException(status=404),
        ]
        step = ApplyFluxResources(api, _DOCS_OCI)
        with patch("migrator.apply_flux_resources.time.sleep"):
            step.revert()
        assert api.get_namespaced_custom_object.call_count == 3
        delete_calls = api.delete_namespaced_custom_object.call_args_list
        assert delete_calls[0].kwargs["plural"] == "helmreleases"
        assert delete_calls[1].kwargs["plural"] == "ocirepositories"

    def test_revert_raises_on_timeout(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {}  # never gone
        step = ApplyFluxResources(api, _DOCS_OCI)
        import migrator.apply_flux_resources as m
        with patch("migrator.apply_flux_resources.time.sleep"), \
             patch.object(m, "_POLL_TIMEOUT_S", 10), \
             patch.object(m, "_POLL_INTERVAL_S", 5):
            with pytest.raises(MigratorError, match="timed out"):
                step.revert()

    def test_revert_does_not_delete_source_when_helm_release_poll_timed_out(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {}  # never gone
        step = ApplyFluxResources(api, _DOCS_OCI)
        import migrator.apply_flux_resources as m
        with patch("migrator.apply_flux_resources.time.sleep"), \
             patch.object(m, "_POLL_TIMEOUT_S", 10), \
             patch.object(m, "_POLL_INTERVAL_S", 5):
            with pytest.raises(MigratorError):
                step.revert()
        delete_calls = api.delete_namespaced_custom_object.call_args_list
        assert all(c.kwargs["plural"] == "helmreleases" for c in delete_calls)

    def test_revert_does_not_delete_source_when_helm_release_delete_failed(self):
        api = MagicMock()
        api.delete_namespaced_custom_object.side_effect = ApiException(status=500)
        step = ApplyFluxResources(api, _DOCS_OCI)
        with pytest.raises(MigratorError, match="delete HelmRelease"):
            step.revert()
        assert api.delete_namespaced_custom_object.call_count == 1

    def test_revert_accumulates_errors_from_suspend_and_source_delete(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        api.delete_namespaced_custom_object.side_effect = [None, ApiException(status=500)]
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        step = ApplyFluxResources(api, _DOCS_OCI)
        with pytest.raises(MigratorError) as exc_info:
            step.revert()
        msg = str(exc_info.value)
        assert "suspend HelmRelease" in msg
        assert "delete OCIRepository" in msg

    def test_revert_raises_on_non_404_poll_error(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=503)
        step = ApplyFluxResources(api, _DOCS_OCI)
        with patch("migrator.apply_flux_resources.time.sleep"):
            with pytest.raises(MigratorError, match="failed to poll HelmRelease"):
                step.revert()
