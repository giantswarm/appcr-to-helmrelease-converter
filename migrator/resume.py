from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import (
    MigratorError,
    _APP_PAUSED_ANNOTATION,
    _CHART_NAMESPACE,
    _CHART_PAUSED_ANNOTATION,
    _CHART_PLURAL,
    _GROUP,
    _PLURAL,
    _VERSION,
    _api_message,
    chart_cr_name,
)


def resume_app_and_chart(
    app_api: client.CustomObjectsApi,
    chart_api: client.CustomObjectsApi,
    app: dict,
) -> list[str]:
    meta = app.get("metadata", {})
    name = meta.get("name", "")
    namespace = meta.get("namespace", "")

    try:
        app_api.patch_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
            name=name, body={"metadata": {"annotations": {_APP_PAUSED_ANNOTATION: None}}},
        )
    except ApiException as e:
        raise MigratorError(f"failed to patch App {namespace}/{name}: {_api_message(e)}") from e

    chart_name = chart_cr_name(app)
    notes = []
    try:
        chart_api.patch_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE, plural=_CHART_PLURAL,
            name=chart_name, body={"metadata": {"annotations": {_CHART_PAUSED_ANNOTATION: None}}},
        )
    except ApiException as e:
        if e.status == 404:
            notes.append(f"Chart {_CHART_NAMESPACE}/{chart_name} not found — skipped, nothing to resume.")
        else:
            raise MigratorError(
                f"failed to patch Chart {_CHART_NAMESPACE}/{chart_name}: {_api_message(e)}"
            ) from e

    return notes
