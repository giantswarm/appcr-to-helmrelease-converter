import base64
from unittest.mock import MagicMock, call, patch

import pytest

from migrator import (
    DisableFluxReconcileApp, MigrationRunner, MigratorError, SuspendApp, SuspendChart,
    chart_cr_name, load_client, load_wc_client,
)
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException


_APP = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {},
    },
}

_APP_FLUX = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {
            "kustomize.toolkit.fluxcd.io/name": "my-app",
            "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
        },
    },
}

_APP_FLUX_RECONCILE_DISABLED = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {
            "kustomize.toolkit.fluxcd.io/name": "my-app",
            "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
            "kustomize.toolkit.fluxcd.io/reconcile": "disabled",
        },
    },
}


class _Stub:
    description = "stub"
    def apply(self): pass
    def revert(self): pass


class TestMigrationRunner:
    def test_run_calls_apply(self):
        step = MagicMock(spec=_Stub)
        runner = MigrationRunner()
        runner.run(step)
        step.apply.assert_called_once()

    def test_run_tracks_step_after_success(self):
        step = MagicMock(spec=_Stub)
        runner = MigrationRunner()
        runner.run(step)
        assert runner._stack == [step]

    def test_run_does_not_track_step_when_apply_raises(self):
        step = MagicMock(spec=_Stub)
        step.apply.side_effect = MigratorError("boom")
        runner = MigrationRunner()
        with pytest.raises(MigratorError):
            runner.run(step)
        assert runner._stack == []

    def test_revert_all_reverts_in_lifo_order(self):
        order = []
        step_a = MagicMock(spec=_Stub)
        step_b = MagicMock(spec=_Stub)
        step_a.revert.side_effect = lambda: order.append("a")
        step_b.revert.side_effect = lambda: order.append("b")
        runner = MigrationRunner()
        runner.run(step_a)
        runner.run(step_b)
        runner.revert_all()
        assert order == ["b", "a"]

    def test_revert_all_continues_on_error_and_raises_combined(self):
        step_a = MagicMock(spec=_Stub)
        step_b = MagicMock(spec=_Stub)
        step_a.revert.side_effect = MigratorError("a failed")
        step_b.revert.side_effect = MigratorError("b failed")
        runner = MigrationRunner()
        runner.run(step_a)
        runner.run(step_b)
        with pytest.raises(MigratorError, match="b failed"):
            runner.revert_all()
        step_a.revert.assert_called_once()  # continued despite b failing first

    def test_revert_all_raises_nothing_when_all_succeed(self):
        step = MagicMock(spec=_Stub)
        runner = MigrationRunner()
        runner.run(step)
        runner.revert_all()  # no exception


class TestDisableFluxReconcileApp:
    def test_skipped_is_true_for_non_flux_app(self):
        step = DisableFluxReconcileApp(MagicMock(), _APP)
        assert step.skipped is True

    def test_skipped_is_false_for_flux_managed_app(self):
        step = DisableFluxReconcileApp(MagicMock(), _APP_FLUX)
        assert step.skipped is False

    def test_skip_message_names_both_flux_labels(self):
        step = DisableFluxReconcileApp(MagicMock(), _APP)
        assert "label" in step.skip_message
        assert "kustomize.toolkit.fluxcd.io/name" in step.skip_message
        assert "kustomize.toolkit.fluxcd.io/namespace" in step.skip_message

    def test_description_contains_namespace_and_name(self):
        step = DisableFluxReconcileApp(MagicMock(), _APP_FLUX)
        assert "giantswarm/my-app" in step.description

    def test_description_contains_reconcile_label(self):
        step = DisableFluxReconcileApp(MagicMock(), _APP_FLUX)
        assert "kustomize.toolkit.fluxcd.io/reconcile" in step.description

    def test_apply_patches_reconcile_label_for_flux_managed_app(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP_FLUX)
        step.apply()
        api.patch_namespaced_custom_object.assert_called_once()
        body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["labels"]["kustomize.toolkit.fluxcd.io/reconcile"] == "disabled"

    def test_apply_does_nothing_for_non_flux_app(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_is_idempotent_when_reconcile_already_disabled(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP_FLUX_RECONCILE_DISABLED)
        step.apply()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_records_did_disable_reconcile_flag(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP_FLUX)
        step.apply()
        assert step._did_disable_reconcile is True

    def test_apply_does_not_set_flag_for_non_flux_app(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP)
        step.apply()
        assert step._did_disable_reconcile is False

    def test_revert_removes_reconcile_label_when_apply_added_it(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP_FLUX)
        step.apply()
        api.reset_mock()
        step.revert()
        body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["labels"]["kustomize.toolkit.fluxcd.io/reconcile"] is None

    def test_revert_does_nothing_when_apply_changed_nothing(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP)  # non-Flux — apply was no-op
        step.apply()
        api.reset_mock()
        step.revert()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_raises_migrator_error_on_api_failure(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        step = DisableFluxReconcileApp(api, _APP_FLUX)
        with pytest.raises(MigratorError, match="failed to patch App"):
            step.apply()

    def test_revert_raises_migrator_error_on_api_failure(self):
        api = MagicMock()
        step = DisableFluxReconcileApp(api, _APP_FLUX)
        step.apply()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with pytest.raises(MigratorError, match="failed to patch App"):
            step.revert()


class TestSuspendApp:
    def test_skipped_is_false_by_default(self):
        step = SuspendApp(MagicMock(), _APP)
        assert step.skipped is False

    def test_apply_patches_paused_annotation(self):
        api = MagicMock()
        step = SuspendApp(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.assert_called_once()
        body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["annotations"]["app-operator.giantswarm.io/paused"] == "true"

    def test_apply_is_idempotent_when_already_paused(self):
        already_paused = {
            "metadata": {
                "name": "my-app",
                "namespace": "giantswarm",
                "annotations": {"app-operator.giantswarm.io/paused": "true"},
                "labels": {},
            },
        }
        api = MagicMock()
        step = SuspendApp(api, already_paused)
        step.apply()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_records_did_pause_flag(self):
        api = MagicMock()
        step = SuspendApp(api, _APP)
        step.apply()
        assert step._did_pause is True

    def test_apply_does_not_set_did_pause_when_already_paused(self):
        already_paused = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {"app-operator.giantswarm.io/paused": "true"},
                "labels": {},
            },
        }
        api = MagicMock()
        step = SuspendApp(api, already_paused)
        step.apply()
        assert step._did_pause is False

    def test_revert_removes_paused_annotation_when_apply_added_it(self):
        api = MagicMock()
        step = SuspendApp(api, _APP)
        step.apply()
        api.reset_mock()
        step.revert()
        body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["annotations"]["app-operator.giantswarm.io/paused"] is None

    def test_revert_does_nothing_when_apply_changed_nothing(self):
        already_paused = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {"app-operator.giantswarm.io/paused": "true"},
                "labels": {},
            },
        }
        api = MagicMock()
        step = SuspendApp(api, already_paused)
        step.apply()
        api.reset_mock()
        step.revert()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_raises_migrator_error_on_api_failure(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        step = SuspendApp(api, _APP)
        with pytest.raises(MigratorError, match="failed to patch App"):
            step.apply()

    def test_revert_raises_migrator_error_on_api_failure(self):
        api = MagicMock()
        step = SuspendApp(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with pytest.raises(MigratorError, match="failed to patch App"):
            step.revert()

    def test_description_contains_namespace_and_name(self):
        step = SuspendApp(MagicMock(), _APP)
        assert "giantswarm/my-app" in step.description

    def test_description_contains_paused_annotation(self):
        step = SuspendApp(MagicMock(), _APP)
        assert "app-operator.giantswarm.io/paused" in step.description


class TestCoreClient:
    def test_returns_core_v1_api(self):
        from migrator import core_client
        mock_api = MagicMock()
        with patch("kubernetes.client.CoreV1Api", return_value=mock_api):
            result = core_client()
        assert result is mock_api


class TestLoadClient:
    def test_returns_custom_objects_api(self):
        mock_api = MagicMock()
        with patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_api):
            result = load_client(None)
        assert result is mock_api

    def test_passes_context_to_load_kube_config(self):
        with patch("kubernetes.config.load_kube_config") as mock_load, \
             patch("kubernetes.client.CustomObjectsApi"):
            load_client("my-context")
        mock_load.assert_called_once_with(context="my-context")

    def test_raises_migrator_error_on_invalid_context(self):
        msg = "Invalid kube-config file. No context found."
        with patch("kubernetes.config.load_kube_config", side_effect=ConfigException(msg)):
            with pytest.raises(MigratorError, match="Invalid kube-config"):
                load_client("bad-context")


class TestChartCRName:
    def test_in_cluster_app_name_unchanged(self):
        app = {"metadata": {"name": "my-app", "labels": {}}}
        assert chart_cr_name(app) == "my-app"

    def test_remote_cluster_strips_prefix(self):
        app = {"metadata": {"name": "my-cluster-myapp", "labels": {"giantswarm.io/cluster": "my-cluster"}}}
        assert chart_cr_name(app) == "myapp"

    def test_remote_cluster_strips_suffix(self):
        app = {"metadata": {"name": "myapp-my-cluster", "labels": {"giantswarm.io/cluster": "my-cluster"}}}
        assert chart_cr_name(app) == "myapp"

    def test_remote_cluster_strips_both_prefix_and_suffix(self):
        app = {"metadata": {"name": "my-cluster-myapp-my-cluster", "labels": {"giantswarm.io/cluster": "my-cluster"}}}
        assert chart_cr_name(app) == "myapp"

    def test_no_labels_field_leaves_name_unchanged(self):
        app = {"metadata": {"name": "my-app"}}
        assert chart_cr_name(app) == "my-app"


_APP_REMOTE = {
    "metadata": {
        "name": "my-cluster-myapp",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {"giantswarm.io/cluster": "my-cluster"},
    },
    "spec": {
        "kubeConfig": {"inCluster": False, "secret": {"name": "my-cluster-kubeconfig", "namespace": "giantswarm"}},
    },
}

_CHART_CR = {
    "metadata": {"name": "my-app", "namespace": "giantswarm", "annotations": {}},
}

_CHART_CR_PAUSED = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {"chart-operator.giantswarm.io/paused": "true"},
    },
}


class TestSuspendChart:
    def _api(self, chart_cr=None):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = chart_cr or _CHART_CR
        return api

    def test_description_contains_giantswarm_namespace_and_chart_name(self):
        step = SuspendChart(self._api(), _APP)
        assert "giantswarm/my-app" in step.description

    def test_description_contains_paused_annotation(self):
        step = SuspendChart(self._api(), _APP)
        assert "chart-operator.giantswarm.io/paused" in step.description

    def test_apply_fetches_chart_cr_to_check_state(self):
        api = self._api()
        step = SuspendChart(api, _APP)
        step.apply()
        api.get_namespaced_custom_object.assert_called_once()
        kwargs = api.get_namespaced_custom_object.call_args.kwargs
        assert kwargs["namespace"] == "giantswarm"
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "charts"

    def test_apply_patches_paused_annotation(self):
        api = self._api()
        step = SuspendChart(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.assert_called_once()
        kwargs = api.patch_namespaced_custom_object.call_args.kwargs
        assert kwargs["namespace"] == "giantswarm"
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "charts"
        assert kwargs["body"]["metadata"]["annotations"]["chart-operator.giantswarm.io/paused"] == "true"

    def test_apply_is_idempotent_when_chart_cr_already_paused(self):
        api = self._api(chart_cr=_CHART_CR_PAUSED)
        step = SuspendChart(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_apply_records_did_pause_flag(self):
        api = self._api()
        step = SuspendChart(api, _APP)
        step.apply()
        assert step._did_pause is True

    def test_apply_does_not_set_flag_when_chart_cr_already_paused(self):
        api = self._api(chart_cr=_CHART_CR_PAUSED)
        step = SuspendChart(api, _APP)
        step.apply()
        assert step._did_pause is False

    def test_apply_raises_migrator_error_on_get_failure(self):
        api = self._api()
        api.get_namespaced_custom_object.side_effect = ApiException(status=403)
        step = SuspendChart(api, _APP)
        with pytest.raises(MigratorError, match="failed to fetch Chart"):
            step.apply()

    def test_apply_raises_migrator_error_on_patch_failure(self):
        api = self._api()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        step = SuspendChart(api, _APP)
        with pytest.raises(MigratorError, match="failed to patch Chart"):
            step.apply()

    def test_revert_removes_paused_annotation_when_apply_added_it(self):
        api = self._api()
        step = SuspendChart(api, _APP)
        step.apply()
        api.reset_mock()
        step.revert()
        body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
        assert body["metadata"]["annotations"]["chart-operator.giantswarm.io/paused"] is None

    def test_revert_does_nothing_when_apply_changed_nothing(self):
        api = self._api(chart_cr=_CHART_CR_PAUSED)
        step = SuspendChart(api, _APP)
        step.apply()
        api.reset_mock()
        step.revert()
        api.patch_namespaced_custom_object.assert_not_called()

    def test_revert_raises_migrator_error_on_api_failure(self):
        api = self._api()
        step = SuspendChart(api, _APP)
        step.apply()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with pytest.raises(MigratorError, match="failed to patch Chart"):
            step.revert()

    def test_apply_uses_wc_api_for_remote_cluster_chart(self):
        api = self._api()
        step = SuspendChart(api, _APP_REMOTE)
        step.apply()
        get_kwargs = api.get_namespaced_custom_object.call_args.kwargs
        patch_kwargs = api.patch_namespaced_custom_object.call_args.kwargs
        assert get_kwargs["name"] == "myapp"
        assert patch_kwargs["name"] == "myapp"


class TestLoadWCClient:
    def _make_secret(self, value):
        secret = MagicMock()
        secret.data = {"value": value}
        return secret

    def test_returns_custom_objects_api_for_wc(self):
        kubeconfig_yaml = b"apiVersion: v1\nclusters: []\ncontexts: []\ncurrent-context: ''\nkind: Config\nusers: []\n"
        core_api = MagicMock()
        core_api.read_namespaced_secret.return_value = self._make_secret(base64.b64encode(kubeconfig_yaml).decode())
        mock_wc_api = MagicMock()
        with patch("kubernetes.config.load_kube_config_from_dict"), \
             patch("kubernetes.client.Configuration"), \
             patch("kubernetes.client.ApiClient"), \
             patch("kubernetes.client.CustomObjectsApi", return_value=mock_wc_api):
            result = load_wc_client(core_api, "my-cluster-kubeconfig", "giantswarm")
        assert result is mock_wc_api

    def test_fetches_secret_by_name_and_namespace(self):
        kubeconfig_yaml = b"apiVersion: v1\nclusters: []\ncontexts: []\ncurrent-context: ''\nkind: Config\nusers: []\n"
        core_api = MagicMock()
        core_api.read_namespaced_secret.return_value = self._make_secret(base64.b64encode(kubeconfig_yaml).decode())
        with patch("kubernetes.config.load_kube_config_from_dict"), \
             patch("kubernetes.client.Configuration"), \
             patch("kubernetes.client.ApiClient"), \
             patch("kubernetes.client.CustomObjectsApi"):
            load_wc_client(core_api, "my-cluster-kubeconfig", "giantswarm")
        core_api.read_namespaced_secret.assert_called_once_with(
            name="my-cluster-kubeconfig", namespace="giantswarm"
        )

    def test_raises_migrator_error_on_secret_fetch_failure(self):
        core_api = MagicMock()
        core_api.read_namespaced_secret.side_effect = ApiException(status=403)
        with pytest.raises(MigratorError, match="failed to fetch kubeconfig secret"):
            load_wc_client(core_api, "my-cluster-kubeconfig", "giantswarm")

    def test_raises_migrator_error_when_value_key_missing(self):
        core_api = MagicMock()
        secret = MagicMock()
        secret.data = {}
        core_api.read_namespaced_secret.return_value = secret
        with pytest.raises(MigratorError, match="no 'value' key"):
            load_wc_client(core_api, "my-cluster-kubeconfig", "giantswarm")
