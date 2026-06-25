from unittest.mock import MagicMock, call, patch

import pytest

from migrator import MonitorHelmRelease, MigratorError
from migrator.monitor_helm_release import _status_message
from kubernetes.client.exceptions import ApiException


_HR = {
    "apiVersion": "helm.toolkit.fluxcd.io/v2",
    "kind": "HelmRelease",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {},
}

_HR_READY = {
    **_HR,
    "status": {
        "conditions": [
            {"type": "Ready", "status": "True", "reason": "InstallSucceeded", "message": "Helm install succeeded"},
        ],
    },
}


class TestMonitorHelmRelease:
    def test_revert_is_noop(self):
        api = MagicMock()
        step = MonitorHelmRelease(api, _HR)
        step.revert()
        api.assert_not_called()

    def test_revert_note_is_none(self):
        step = MonitorHelmRelease(MagicMock(), _HR)
        assert step.revert_note is None

    def test_apply_returns_when_already_ready(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = _HR_READY
        step = MonitorHelmRelease(api, _HR, interval=1, timeout=10)
        step.apply()
        api.get_namespaced_custom_object.assert_called_once_with(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            namespace="giantswarm",
            plural="helmreleases",
            name="my-app",
        )

    def test_apply_raises_immediately_when_stalled(self):
        api = MagicMock()
        hr_stalled = {**_HR, "status": {"conditions": [
            {"type": "Stalled", "status": "True", "reason": "RetriesExceeded",
             "message": "Failed to install after 10 attempt(s)"},
            {"type": "Ready", "status": "False", "reason": "InstallFailed", "message": "chart not found"},
        ]}}
        api.get_namespaced_custom_object.return_value = hr_stalled
        step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
        with pytest.raises(MigratorError, match="Failed to install after 10 attempt"):
            step.apply()
        api.get_namespaced_custom_object.assert_called_once()

    def test_apply_raises_on_timeout(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {**_HR, "status": {"conditions": []}}
        with patch("migrator.monitor_helm_release.time.sleep"):
            step = MonitorHelmRelease(api, _HR, interval=1, timeout=3)
            with pytest.raises(MigratorError, match="timed out"):
                step.apply()

    def test_apply_raises_after_fetch_retries_exhausted(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=503, reason="Service Unavailable")
        with patch("migrator.monitor_helm_release.time.sleep"):
            step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
            with pytest.raises(MigratorError, match="failed to get HelmRelease"):
                step.apply()

    def test_apply_raises_immediately_on_permanent_4xx(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404, reason="Not Found")
        step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
        with pytest.raises(MigratorError, match="failed to get HelmRelease"):
            step.apply()
        api.get_namespaced_custom_object.assert_called_once()

    def test_apply_tolerates_transient_fetch_errors(self):
        api = MagicMock()
        err = ApiException(status=503, reason="Service Unavailable")
        api.get_namespaced_custom_object.side_effect = [err, err, _HR_READY]
        with patch("migrator.monitor_helm_release.time.sleep"):
            step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
            step.apply()

    def test_apply_raises_on_keyboard_interrupt(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = KeyboardInterrupt
        step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
        with pytest.raises(MigratorError, match="interrupted"):
            step.apply()

    def test_apply_updates_spinner_with_status_messages(self):
        api = MagicMock()
        _HR_RETRYING = {**_HR, "status": {
            "installFailures": 1,
            "conditions": [
                {"type": "Released", "status": "False", "reason": "InstallFailed", "message": "chart not found"},
                {"type": "Reconciling", "status": "True", "reason": "ProgressingWithRetry", "message": "retrying after 5m"},
                {"type": "Ready", "status": "Unknown", "reason": "ProgressingWithRetry"},
            ],
        }}
        api.get_namespaced_custom_object.side_effect = [_HR_RETRYING, _HR_READY]
        mock_status = MagicMock()
        mock_console = MagicMock()
        mock_console.status.return_value.__enter__ = MagicMock(return_value=mock_status)
        mock_console.status.return_value.__exit__ = MagicMock(return_value=False)
        with patch("migrator.monitor_helm_release.time.sleep"):
            step = MonitorHelmRelease(api, _HR, interval=1, timeout=30, console=mock_console)
            step.apply()
        mock_status.update.assert_called()
        messages = [call.args[0] for call in mock_status.update.call_args_list]
        assert any("attempt 1" in m or "install" in m.lower() for m in messages)

    def test_description_includes_namespace_and_name(self):
        step = MonitorHelmRelease(MagicMock(), _HR)
        assert "giantswarm" in step.description
        assert "my-app" in step.description

    def test_apply_polls_until_ready(self):
        api = MagicMock()
        _HR_NOT_READY = {**_HR, "status": {"conditions": [
            {"type": "Ready", "status": "Unknown", "reason": "Progressing"},
        ]}}
        api.get_namespaced_custom_object.side_effect = [_HR_NOT_READY, _HR_NOT_READY, _HR_READY]
        with patch("migrator.monitor_helm_release.time.sleep"):
            step = MonitorHelmRelease(api, _HR, interval=1, timeout=30)
            step.apply()
        assert api.get_namespaced_custom_object.call_count == 3


class TestStatusMessage:
    def test_progressing_action(self):
        status = {"conditions": [
            {"type": "Reconciling", "status": "True", "reason": "Progressing"},
        ]}
        msg = _status_message(status)
        assert "installing" in msg.lower()

    def test_retrying_with_no_failed_release(self):
        status = {"installFailures": 0, "conditions": [
            {"type": "Reconciling", "status": "True", "reason": "ProgressingWithRetry"},
        ]}
        msg = _status_message(status)
        assert "retrying" in msg.lower()
        assert ":" not in msg  # no failure detail appended

    def test_released_failed_no_reconciling(self):
        status = {"conditions": [
            {"type": "Released", "status": "False", "reason": "InstallFailed", "message": "image pull failed"},
        ]}
        msg = _status_message(status)
        assert "install failed" in msg.lower()
        assert "image pull failed" in msg

    def test_no_conditions_returns_generic_waiting_message(self):
        msg = _status_message({"conditions": []})
        assert "waiting" in msg.lower()
