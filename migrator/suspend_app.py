from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import MigrationStep, MigratorError, _APP_PAUSED_ANNOTATION, _GROUP, _PLURAL, _VERSION, _api_message


class SuspendApp(MigrationStep):
    def __init__(self, api: client.CustomObjectsApi, app: dict):
        self._api = api
        self._app = app
        self._did_pause = False
        self._revert_note: str | None = None

    @property
    def description(self) -> str:
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        return f'Annotate App CR {namespace}/{name} with app-operator.giantswarm.io/paused: "true"'

    def apply(self) -> None:
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        annotations = meta.get("annotations") or {}
        if annotations.get(_APP_PAUSED_ANNOTATION) == "true":
            self._revert_note = (
                f"ℹ️  App CR {namespace}/{name} was already paused before this run — not reverted.\n"
                f"    To unpause:  kubectl annotate app {name} -n {namespace} app-operator.giantswarm.io/paused-"
            )
            return
        self._did_pause = True
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
                name=name, body={"metadata": {"annotations": {_APP_PAUSED_ANNOTATION: "true"}}},
            )
        except ApiException as e:
            raise MigratorError(f"failed to patch App {namespace}/{name}: {_api_message(e)}") from e

    @property
    def revert_note(self) -> str | None:
        return self._revert_note

    def revert(self) -> None:
        if not self._did_pause:
            return
        meta = self._app.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
                name=name, body={"metadata": {"annotations": {_APP_PAUSED_ANNOTATION: None}}},
            )
        except ApiException as e:
            raise MigratorError(f"failed to patch App {namespace}/{name}: {_api_message(e)}") from e
