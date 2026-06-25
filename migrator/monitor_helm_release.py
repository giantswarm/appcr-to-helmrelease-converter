import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import MigrationStep, MigratorError, _api_message, _HR_GROUP, _HR_VERSION, _HR_PLURAL, _POLL_INTERVAL_S, _POLL_TIMEOUT_S
_FETCH_RETRIES = 3


def _get_condition(conditions: list, ctype: str) -> dict | None:
    for c in conditions:
        if c.get("type") == ctype:
            return c
    return None


def _status_message(status: dict) -> str:
    conditions = status.get("conditions") or []
    install_failures = status.get("installFailures", 0)
    upgrade_failures = status.get("upgradeFailures", 0)
    failures = install_failures + upgrade_failures
    action = status.get("lastAttemptedReleaseAction", "install")

    reconciling = _get_condition(conditions, "Reconciling")
    released = _get_condition(conditions, "Released")

    if reconciling and reconciling.get("status") == "True":
        reason = reconciling.get("reason", "")
        if reason == "ProgressingWithRetry":
            failure_msg = ""
            if released and released.get("status") == "False":
                failure_msg = f": {released.get('message', released.get('reason', ''))}"
            return f"{action.capitalize()} failed (attempt {failures}){failure_msg}. Retrying…"
        return f"{action.capitalize()}ing… (attempt {failures + 1})"

    if released and released.get("status") == "False":
        return f"{action.capitalize()} failed: {released.get('message', released.get('reason', 'unknown'))}"

    return f"Waiting for HelmRelease to become ready…"


class MonitorHelmRelease(MigrationStep):
    def __init__(
        self,
        api: client.CustomObjectsApi,
        hr: dict,
        interval: int = _POLL_INTERVAL_S,
        timeout: int = _POLL_TIMEOUT_S,
        console=None,
    ):
        self._api = api
        self._interval = interval
        self._timeout = timeout
        self._console = console
        meta = hr.get("metadata", {})
        self._name = meta.get("name", "")
        self._namespace = meta.get("namespace", "")

    @property
    def description(self) -> str:
        return f"Monitor HelmRelease {self._namespace}/{self._name}"

    def apply(self) -> None:
        try:
            if self._console:
                with self._console.status(f"Waiting for HelmRelease {self._namespace}/{self._name}…") as spinner:
                    self._poll(spinner)
            else:
                self._poll(None)
        except KeyboardInterrupt:
            raise MigratorError(
                f"monitoring of HelmRelease {self._namespace}/{self._name} interrupted by user"
            )

    def _poll(self, spinner) -> None:
        steps = int(self._timeout / self._interval)
        for _ in range(steps):
            hr = self._fetch()
            status = hr.get("status") or {}
            conditions = status.get("conditions") or []

            ready = _get_condition(conditions, "Ready")
            if ready and ready.get("status") == "True":
                return

            stalled = _get_condition(conditions, "Stalled")
            if stalled and stalled.get("status") == "True":
                raise MigratorError(
                    f"HelmRelease {self._namespace}/{self._name} stalled: {stalled.get('message', stalled.get('reason', 'unknown'))}"
                )

            if spinner is not None:
                spinner.update(_status_message(status))

            time.sleep(self._interval)

        raise MigratorError(
            f"timed out waiting for HelmRelease {self._namespace}/{self._name} to become ready"
        )

    def _fetch(self) -> dict:
        last_err = None
        for _ in range(_FETCH_RETRIES):
            try:
                return self._api.get_namespaced_custom_object(
                    group=_HR_GROUP,
                    version=_HR_VERSION,
                    namespace=self._namespace,
                    plural=_HR_PLURAL,
                    name=self._name,
                )
            except ApiException as e:
                if e.status is not None and 400 <= e.status < 500:
                    raise MigratorError(
                        f"failed to get HelmRelease {self._namespace}/{self._name}: {_api_message(e)}"
                    ) from e
                last_err = e
                time.sleep(self._interval)
        raise MigratorError(
            f"failed to get HelmRelease {self._namespace}/{self._name}: {_api_message(last_err)}"
        ) from last_err

    def revert(self) -> None:
        pass
