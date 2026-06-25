import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import (
    MigratorError,
    _GROUP,
    _VERSION,
    _PLURAL,
    _CHART_PLURAL,
    _api_message,
    chart_cr_name,
)

_CHART_NAMESPACE = "giantswarm"
_APP_FINALIZER = "operatorkit.giantswarm.io/app-operator-app"
_CHART_FINALIZER = "operatorkit.giantswarm.io/chart-operator-chart"
_APP_PAUSED_ANNOTATION = "app-operator.giantswarm.io/paused"
_CHART_PAUSED_ANNOTATION = "chart-operator.giantswarm.io/paused"

_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 300


def flux_cleanup_message(app: dict) -> str:
    meta = app.get("metadata", {})
    name = meta.get("name", "")
    namespace = meta.get("namespace", "")
    chart_name = chart_cr_name(app)

    kube = (app.get("spec") or {}).get("kubeConfig") or {}
    is_remote = kube.get("inCluster") is False
    if is_remote:
        secret = kube.get("secret") or {}
        secret_name = secret.get("name", "")
        secret_ns = secret.get("namespace", namespace)
        chart_cr_header = (
            f"  # Remove finalizer {_CHART_FINALIZER} and delete Chart CR\n"
            f"  # (run against the WORKLOAD cluster — "
            f"kubeconfig available in Secret {secret_ns}/{secret_name} on the MC):"
        )
    else:
        chart_cr_header = f"  # Remove finalizer {_CHART_FINALIZER} and delete Chart CR:"

    return (
        f"Migration complete. The App CR and Chart CR must be deleted manually because this app "
        f"is managed by Flux — deleting automatically would cause the Kustomization to recreate "
        f"them and undo the migration. Commit the generated Flux resources to your gitops "
        f"repository and remove the App CR from it first, then run:\n\n"
        f"{chart_cr_header}\n"
        f"  kubectl patch chart -n {_CHART_NAMESPACE} {chart_name} --type=merge "
        f"-p '{{\"metadata\":{{\"finalizers\":[]}}}}'\n"
        f"  kubectl delete chart -n {_CHART_NAMESPACE} {chart_name}\n\n"
        f"  # Remove finalizer {_APP_FINALIZER} and delete App CR:\n"
        f"  kubectl patch app -n {namespace} {name} --type=merge "
        f"-p '{{\"metadata\":{{\"finalizers\":[]}}}}'\n"
        f"  kubectl delete app -n {namespace} {name}\n"
    )


def _ensure_paused(api: client.CustomObjectsApi, group: str, version: str,
                   namespace: str, plural: str, name: str,
                   annotation_key: str, live: dict) -> None:
    annotations = (live.get("metadata") or {}).get("annotations") or {}
    if annotations.get(annotation_key) == "true":
        return
    try:
        api.patch_namespaced_custom_object(
            group=group, version=version, namespace=namespace, plural=plural,
            name=name, body={"metadata": {"annotations": {annotation_key: "true"}}},
        )
    except ApiException as e:
        raise MigratorError(
            f"failed to re-apply {annotation_key} on {plural} {namespace}/{name}: {_api_message(e)}"
        ) from e


def _remove_finalizer_and_delete(api: client.CustomObjectsApi, group: str, version: str,
                                  namespace: str, plural: str, name: str,
                                  finalizer: str, live: dict) -> None:
    current = list((live.get("metadata") or {}).get("finalizers") or [])
    new_finalizers = [f for f in current if f != finalizer]
    try:
        api.patch_namespaced_custom_object(
            group=group, version=version, namespace=namespace, plural=plural,
            name=name, body={"metadata": {"finalizers": new_finalizers}},
        )
    except ApiException as e:
        raise MigratorError(
            f"failed to remove finalizer from {plural} {namespace}/{name}: {_api_message(e)}"
        ) from e
    try:
        api.delete_namespaced_custom_object(
            group=group, version=version, namespace=namespace, plural=plural, name=name,
        )
    except ApiException as e:
        raise MigratorError(
            f"failed to delete {plural} {namespace}/{name}: {_api_message(e)}"
        ) from e


def _poll_until_gone(api: client.CustomObjectsApi, group: str, version: str,
                     namespace: str, plural: str, name: str) -> None:
    for _ in range(int(_POLL_TIMEOUT_S / _POLL_INTERVAL_S)):
        try:
            api.get_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural, name=name,
            )
        except ApiException as e:
            if e.status == 404:
                return
            raise MigratorError(
                f"failed to poll {plural} {namespace}/{name}: {_api_message(e)}"
            ) from e
        time.sleep(_POLL_INTERVAL_S)
    raise MigratorError(
        f"timed out waiting for {plural} {namespace}/{name} to be deleted; "
        "check finalizers or delete manually"
    )


def delete_app_and_chart(
    app_api: client.CustomObjectsApi,
    chart_api: client.CustomObjectsApi,
    app: dict,
) -> None:
    meta = app.get("metadata", {})
    name = meta.get("name", "")
    namespace = meta.get("namespace", "")
    chart_name = chart_cr_name(app)

    try:
        live_app = app_api.get_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL, name=name,
        )
    except ApiException as e:
        raise MigratorError(f"failed to fetch App {namespace}/{name}: {_api_message(e)}") from e

    try:
        live_chart = chart_api.get_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE,
            plural=_CHART_PLURAL, name=chart_name,
        )
    except ApiException as e:
        raise MigratorError(
            f"failed to fetch Chart {_CHART_NAMESPACE}/{chart_name}: {_api_message(e)}"
        ) from e

    _ensure_paused(app_api, _GROUP, _VERSION, namespace, _PLURAL, name, _APP_PAUSED_ANNOTATION, live_app)
    _ensure_paused(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name, _CHART_PAUSED_ANNOTATION, live_chart)

    errors = []

    # Chart CR first
    try:
        _remove_finalizer_and_delete(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name, _CHART_FINALIZER, live_chart)
        _poll_until_gone(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name)
    except MigratorError as e:
        errors.append(e)

    # App CR second
    try:
        _remove_finalizer_and_delete(app_api, _GROUP, _VERSION, namespace, _PLURAL, name, _APP_FINALIZER, live_app)
        _poll_until_gone(app_api, _GROUP, _VERSION, namespace, _PLURAL, name)
    except MigratorError as e:
        errors.append(e)

    if errors:
        raise MigratorError("; ".join(str(e) for e in errors))
