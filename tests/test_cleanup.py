import time
from unittest.mock import MagicMock, call, patch

import pytest

from kubernetes.client.exceptions import ApiException

from migrator import MigratorError
from migrator.cleanup import delete_app_and_chart, flux_cleanup_message


_APP = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {},
        "finalizers": ["operatorkit.giantswarm.io/app-operator-app"],
    },
}

_APP_REMOTE = {
    "metadata": {
        "name": "my-cluster-myapp",
        "namespace": "giantswarm",
        "annotations": {},
        "labels": {"giantswarm.io/cluster": "my-cluster"},
        "finalizers": ["operatorkit.giantswarm.io/app-operator-app"],
    },
    "spec": {
        "kubeConfig": {
            "inCluster": False,
            "secret": {"name": "my-cluster-kubeconfig", "namespace": "giantswarm"},
        },
    },
}

_CHART_CR = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {"chart-operator.giantswarm.io/paused": "true"},
        "finalizers": ["operatorkit.giantswarm.io/chart-operator-chart"],
    },
}

_APP_CR_LIVE = {
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "annotations": {"app-operator.giantswarm.io/paused": "true"},
        "finalizers": ["operatorkit.giantswarm.io/app-operator-app"],
    },
}


class TestFluxCleanupMessage:
    def test_contains_app_cr_namespace_and_name(self):
        msg = flux_cleanup_message(_APP)
        assert "giantswarm" in msg
        assert "my-app" in msg

    def test_contains_chart_cr_name(self):
        msg = flux_cleanup_message(_APP)
        assert "my-app" in msg

    def test_contains_app_cr_finalizer(self):
        msg = flux_cleanup_message(_APP)
        assert "operatorkit.giantswarm.io/app-operator-app" in msg

    def test_contains_chart_cr_finalizer(self):
        msg = flux_cleanup_message(_APP)
        assert "operatorkit.giantswarm.io/chart-operator-chart" in msg

    def test_remote_cluster_mentions_kubeconfig_secret(self):
        msg = flux_cleanup_message(_APP_REMOTE)
        assert "my-cluster-kubeconfig" in msg
        assert "giantswarm" in msg

    def test_in_cluster_app_has_no_kubeconfig_secret_mention(self):
        msg = flux_cleanup_message(_APP)
        assert "kubeconfig" not in msg.lower() and "secret" not in msg.lower()


class TestDeleteAppAndChart:
    def _apis(self, app_cr=None, chart_cr=None):
        app_api = MagicMock()
        chart_api = MagicMock()
        # first call = re-fetch, second call = poll → 404 (object gone)
        app_api.get_namespaced_custom_object.side_effect = [
            app_cr or _APP_CR_LIVE,
            ApiException(status=404),
        ]
        chart_api.get_namespaced_custom_object.side_effect = [
            chart_cr or _CHART_CR,
            ApiException(status=404),
        ]
        app_api.delete_namespaced_custom_object.return_value = None
        chart_api.delete_namespaced_custom_object.return_value = None
        return app_api, chart_api

    def test_refetches_app_cr_from_cluster(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        first_call = app_api.get_namespaced_custom_object.call_args_list[0]
        assert first_call.kwargs["name"] == "my-app"
        assert first_call.kwargs["namespace"] == "giantswarm"
        assert first_call.kwargs["plural"] == "apps"

    def test_refetches_chart_cr_from_cluster(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        first_call = chart_api.get_namespaced_custom_object.call_args_list[0]
        assert first_call.kwargs["name"] == "my-app"
        assert first_call.kwargs["namespace"] == "giantswarm"
        assert first_call.kwargs["plural"] == "charts"

    def test_reapplies_app_paused_annotation_when_missing(self):
        live_app_no_pause = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {},
                "finalizers": ["operatorkit.giantswarm.io/app-operator-app"],
            },
        }
        app_api, chart_api = self._apis(app_cr=live_app_no_pause)
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        patch_calls = app_api.patch_namespaced_custom_object.call_args_list
        pause_call = next(
            (c for c in patch_calls
             if (c.kwargs.get("body") or {}).get("metadata", {}).get("annotations", {}).get("app-operator.giantswarm.io/paused") == "true"),
            None,
        )
        assert pause_call is not None

    def test_skips_app_paused_patch_when_already_set(self):
        app_api, chart_api = self._apis()  # _APP_CR_LIVE already has paused=true
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        patch_calls = app_api.patch_namespaced_custom_object.call_args_list
        pause_calls = [
            c for c in patch_calls
            if (c.kwargs.get("body") or {}).get("metadata", {}).get("annotations", {}).get("app-operator.giantswarm.io/paused") == "true"
        ]
        assert len(pause_calls) == 0

    def test_reapplies_chart_paused_annotation_when_missing(self):
        live_chart_no_pause = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {},
                "finalizers": ["operatorkit.giantswarm.io/chart-operator-chart"],
            },
        }
        app_api, chart_api = self._apis(chart_cr=live_chart_no_pause)
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        patch_calls = chart_api.patch_namespaced_custom_object.call_args_list
        pause_call = next(
            (c for c in patch_calls
             if (c.kwargs.get("body") or {}).get("metadata", {}).get("annotations", {}).get("chart-operator.giantswarm.io/paused") == "true"),
            None,
        )
        assert pause_call is not None

    def test_removes_chart_cr_finalizer_before_deleting(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        patch_calls = chart_api.patch_namespaced_custom_object.call_args_list
        finalizer_patch = next(
            (c for c in patch_calls
             if "finalizers" in (c.kwargs.get("body") or {}).get("metadata", {})),
            None,
        )
        assert finalizer_patch is not None
        finalizers = finalizer_patch.kwargs["body"]["metadata"]["finalizers"]
        assert "operatorkit.giantswarm.io/chart-operator-chart" not in finalizers

    def test_deletes_chart_cr(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        chart_api.delete_namespaced_custom_object.assert_called_once()
        kwargs = chart_api.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "charts"

    def test_polls_until_chart_cr_is_gone(self):
        app_api, chart_api = self._apis()
        chart_api.get_namespaced_custom_object.side_effect = [
            _CHART_CR,           # initial re-fetch
            _CHART_CR,           # first poll: still there
            ApiException(status=404),  # second poll: gone
        ]
        app_api.get_namespaced_custom_object.side_effect = [
            _APP_CR_LIVE,        # re-fetch
            ApiException(status=404),  # app poll: gone
        ]
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        assert chart_api.get_namespaced_custom_object.call_count == 3

    def test_removes_app_cr_finalizer_before_deleting(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        patch_calls = app_api.patch_namespaced_custom_object.call_args_list
        finalizer_patch = next(
            (c for c in patch_calls
             if "finalizers" in (c.kwargs.get("body") or {}).get("metadata", {})),
            None,
        )
        assert finalizer_patch is not None
        finalizers = finalizer_patch.kwargs["body"]["metadata"]["finalizers"]
        assert "operatorkit.giantswarm.io/app-operator-app" not in finalizers

    def test_deletes_app_cr(self):
        app_api, chart_api = self._apis()
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        app_api.delete_namespaced_custom_object.assert_called_once()
        kwargs = app_api.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "apps"

    def test_polls_until_app_cr_is_gone(self):
        app_api, chart_api = self._apis()
        app_api.get_namespaced_custom_object.side_effect = [
            _APP_CR_LIVE,        # re-fetch
            _APP_CR_LIVE,        # poll: still there
            ApiException(status=404),  # poll: gone
        ]
        chart_api.get_namespaced_custom_object.side_effect = [
            _CHART_CR,
            ApiException(status=404),
        ]
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        assert app_api.get_namespaced_custom_object.call_count == 3

    def test_deletes_chart_cr_before_app_cr(self):
        order = []
        app_api, chart_api = self._apis()
        chart_api.delete_namespaced_custom_object.side_effect = lambda **kw: order.append("chart")
        app_api.delete_namespaced_custom_object.side_effect = lambda **kw: order.append("app")
        with patch("migrator.cleanup.time.sleep"):
            delete_app_and_chart(app_api, chart_api, _APP)
        assert order == ["chart", "app"]

    def test_continues_to_app_cr_when_chart_cr_fails_then_raises_combined(self):
        app_api, chart_api = self._apis()
        chart_api.delete_namespaced_custom_object.side_effect = ApiException(status=500)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError) as exc_info:
                delete_app_and_chart(app_api, chart_api, _APP)
        # Chart failed but App deletion was still attempted
        app_api.delete_namespaced_custom_object.assert_called_once()
        assert "chart" in str(exc_info.value).lower() or "Chart" in str(exc_info.value)

    def test_raises_on_poll_timeout_for_chart_cr(self):
        app_api, chart_api = self._apis()
        # override side_effect so poll never returns 404
        chart_api.get_namespaced_custom_object.side_effect = None
        chart_api.get_namespaced_custom_object.return_value = _CHART_CR
        import migrator.cleanup as m
        with patch("migrator.cleanup.time.sleep"), \
             patch.object(m, "_POLL_TIMEOUT_S", 10), \
             patch.object(m, "_POLL_INTERVAL_S", 5):
            with pytest.raises(MigratorError, match="timed out"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_app_cr_fetch_fails(self):
        app_api, chart_api = self._apis()
        app_api.get_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to fetch App"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_chart_cr_fetch_fails(self):
        app_api, chart_api = self._apis()
        chart_api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to fetch Chart"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_ensure_paused_patch_fails(self):
        live_app_no_pause = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {},
                "finalizers": ["operatorkit.giantswarm.io/app-operator-app"],
            },
        }
        app_api, chart_api = self._apis(app_cr=live_app_no_pause)
        app_api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to re-apply"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_finalizer_patch_fails(self):
        app_api, chart_api = self._apis()
        chart_api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to remove finalizer"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_on_non_404_poll_error_for_chart_cr(self):
        app_api, chart_api = self._apis()
        chart_api.get_namespaced_custom_object.side_effect = [
            _CHART_CR,
            ApiException(status=503),
        ]
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to poll"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_app_cr_deletion_fails(self):
        app_api, chart_api = self._apis()
        app_api.delete_namespaced_custom_object.side_effect = ApiException(status=500)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to delete"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_when_chart_ensure_paused_patch_fails(self):
        live_chart_no_pause = {
            "metadata": {
                "name": "my-app", "namespace": "giantswarm",
                "annotations": {},
                "finalizers": ["operatorkit.giantswarm.io/chart-operator-chart"],
            },
        }
        app_api, chart_api = self._apis(chart_cr=live_chart_no_pause)
        chart_api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with patch("migrator.cleanup.time.sleep"):
            with pytest.raises(MigratorError, match="failed to re-apply"):
                delete_app_and_chart(app_api, chart_api, _APP)

    def test_raises_on_poll_timeout_for_app_cr(self):
        app_api, chart_api = self._apis()
        app_api.get_namespaced_custom_object.side_effect = None
        app_api.get_namespaced_custom_object.return_value = _APP_CR_LIVE
        import migrator.cleanup as m
        with patch("migrator.cleanup.time.sleep"), \
             patch.object(m, "_POLL_TIMEOUT_S", 10), \
             patch.object(m, "_POLL_INTERVAL_S", 5):
            with pytest.raises(MigratorError, match="timed out"):
                delete_app_and_chart(app_api, chart_api, _APP)
