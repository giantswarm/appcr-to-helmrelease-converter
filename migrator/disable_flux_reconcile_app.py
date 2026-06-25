from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import (
    MigrationStep,
    MigratorError,
    _FLUX_NAME_LABEL,
    _FLUX_NS_LABEL,
    _FLUX_RECONCILE_LABEL,
    _GROUP,
    _PLURAL,
    _VERSION,
    _api_message,
)


class DisableFluxReconcileApp(MigrationStep):
    def __init__(self, api: client.CustomObjectsApi, app: dict):
        self._api = api
        self._app = app
        self._did_disable_reconcile = False
        self._revert_note: str | None = None
        labels = (app.get("metadata", {}).get("labels") or {})
        self._is_flux_managed = _FLUX_NAME_LABEL in labels and _FLUX_NS_LABEL in labels

    @property
    def description(self) -> str:
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        return f'Annotate App CR {namespace}/{name} with {_FLUX_RECONCILE_LABEL}: "disabled"'

    @property
    def skipped(self) -> bool:
        return not self._is_flux_managed

    @property
    def skip_message(self) -> str:
        return (
            f"App CR labels {_FLUX_NAME_LABEL} and {_FLUX_NS_LABEL} not detected — step skipped"
        )

    def apply(self) -> None:
        if not self._is_flux_managed:
            return
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        labels = meta.get("labels") or {}
        if labels.get(_FLUX_RECONCILE_LABEL) == "disabled":
            self._revert_note = (
                f"ℹ️  App CR {namespace}/{name} already had Flux reconcile disabled before this run — not reverted.\n"
                f"    To re-enable:  kubectl label app {name} -n {namespace} {_FLUX_RECONCILE_LABEL}-"
            )
            return
        self._did_disable_reconcile = True
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
                name=name, body={"metadata": {"labels": {_FLUX_RECONCILE_LABEL: "disabled"}}},
            )
        except ApiException as e:
            raise MigratorError(f"failed to patch App {namespace}/{name}: {_api_message(e)}") from e

    @property
    def revert_note(self) -> str | None:
        return self._revert_note

    def revert(self) -> None:
        if not self._did_disable_reconcile:
            return
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
                name=name, body={"metadata": {"labels": {_FLUX_RECONCILE_LABEL: None}}},
            )
        except ApiException as e:
            raise MigratorError(f"failed to patch App {namespace}/{name}: {_api_message(e)}") from e
