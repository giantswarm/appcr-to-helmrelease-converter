from unittest.mock import MagicMock

import pytest

from kubernetes.client.exceptions import ApiException

from migrator import MigratorError
from migrator.resume import resume_app_and_chart


_APP = {
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
}

_APP_REMOTE = {
    "metadata": {
        "name": "my-cluster-myapp",
        "namespace": "giantswarm",
        "labels": {"giantswarm.io/cluster": "my-cluster"},
    },
    "spec": {"kubeConfig": {"inCluster": False}},
}


class TestResumeAppAndChart:
    def test_clears_app_paused_annotation(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        resume_app_and_chart(app_api, chart_api, _APP)
        kwargs = app_api.patch_namespaced_custom_object.call_args.kwargs
        assert kwargs["namespace"] == "giantswarm"
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "apps"
        assert kwargs["body"]["metadata"]["annotations"]["app-operator.giantswarm.io/paused"] is None

    def test_raises_migrator_error_on_app_patch_failure(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        app_api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with pytest.raises(MigratorError, match="failed to patch App"):
            resume_app_and_chart(app_api, chart_api, _APP)

    def test_clears_chart_paused_annotation(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        resume_app_and_chart(app_api, chart_api, _APP)
        kwargs = chart_api.patch_namespaced_custom_object.call_args.kwargs
        assert kwargs["namespace"] == "giantswarm"
        assert kwargs["name"] == "my-app"
        assert kwargs["plural"] == "charts"
        assert kwargs["body"]["metadata"]["annotations"]["chart-operator.giantswarm.io/paused"] is None

    def test_returns_empty_notes_on_success(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        assert resume_app_and_chart(app_api, chart_api, _APP) == []

    def test_chart_404_returns_skip_note_instead_of_raising(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        chart_api.patch_namespaced_custom_object.side_effect = ApiException(status=404)
        notes = resume_app_and_chart(app_api, chart_api, _APP)
        assert len(notes) == 1
        assert "giantswarm/my-app" in notes[0]

    def test_raises_migrator_error_on_chart_patch_failure_non_404(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        chart_api.patch_namespaced_custom_object.side_effect = ApiException(status=500)
        with pytest.raises(MigratorError, match="failed to patch Chart"):
            resume_app_and_chart(app_api, chart_api, _APP)

    def test_derives_chart_name_by_stripping_cluster_prefix(self):
        app_api = MagicMock()
        chart_api = MagicMock()
        resume_app_and_chart(app_api, chart_api, _APP_REMOTE)
        kwargs = chart_api.patch_namespaced_custom_object.call_args.kwargs
        assert kwargs["name"] == "myapp"
