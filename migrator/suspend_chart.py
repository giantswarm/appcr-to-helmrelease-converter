from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import (
    MigrationStep,
    MigratorError,
    _CHART_NAMESPACE,
    _CHART_PAUSED_ANNOTATION,
    _CHART_PLURAL,
    _GROUP,
    _VERSION,
    _api_message,
    chart_cr_name,
)


class SuspendChart(MigrationStep):
    def __init__(self, api: client.CustomObjectsApi, app: dict):
        self._api = api
        self._app = app
        self._did_pause = False
        self._revert_note: str | None = None
        self._chart_name = chart_cr_name(app)

    @property
    def description(self) -> str:
        return f'Annotate Chart CR {_CHART_NAMESPACE}/{self._chart_name} with {_CHART_PAUSED_ANNOTATION}: "true"'

    def apply(self) -> None:
        try:
            chart = self._api.get_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE, plural=_CHART_PLURAL,
                name=self._chart_name,
            )
        except ApiException as e:
            raise MigratorError(
                f"failed to fetch Chart {_CHART_NAMESPACE}/{self._chart_name}: {_api_message(e)}"
            ) from e
        annotations = (chart.get("metadata", {}).get("annotations") or {})
        if annotations.get(_CHART_PAUSED_ANNOTATION) == "true":
            kube = (self._app.get("spec") or {}).get("kubeConfig") or {}
            if kube.get("inCluster") is False:
                secret = kube.get("secret") or {}
                secret_name = secret.get("name", "")
                secret_ns = secret.get("namespace") or (self._app.get("metadata") or {}).get("namespace", _CHART_NAMESPACE)
                wc_hint = f"\n    WC kubeconfig secret:  {secret_ns}/{secret_name}"
            else:
                wc_hint = ""
            self._revert_note = (
                f"ℹ️  Chart CR {_CHART_NAMESPACE}/{self._chart_name} was already paused before this run — not reverted.{wc_hint}\n"
                f"    To unpause:  kubectl annotate chart {self._chart_name} -n {_CHART_NAMESPACE} {_CHART_PAUSED_ANNOTATION}-"
            )
            return
        self._did_pause = True
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE, plural=_CHART_PLURAL,
                name=self._chart_name,
                body={"metadata": {"annotations": {_CHART_PAUSED_ANNOTATION: "true"}}},
            )
        except ApiException as e:
            raise MigratorError(
                f"failed to patch Chart {_CHART_NAMESPACE}/{self._chart_name}: {_api_message(e)}"
            ) from e

    @property
    def revert_note(self) -> str | None:
        return self._revert_note

    def revert(self) -> None:
        if not self._did_pause:
            return
        try:
            self._api.patch_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE, plural=_CHART_PLURAL,
                name=self._chart_name,
                body={"metadata": {"annotations": {_CHART_PAUSED_ANNOTATION: None}}},
            )
        except ApiException as e:
            raise MigratorError(
                f"failed to patch Chart {_CHART_NAMESPACE}/{self._chart_name}: {_api_message(e)}"
            ) from e
