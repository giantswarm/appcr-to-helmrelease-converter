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
