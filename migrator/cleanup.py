import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import (
    MigratorError,
    _APP_PAUSED_ANNOTATION,
    _CHART_NAMESPACE,
    _CHART_PAUSED_ANNOTATION,
    _FLUX_NAME_LABEL,
    _FLUX_NS_LABEL,
    _FLUX_RECONCILE_LABEL,
    _GROUP,
    _HR_GROUP,
    _HR_PLURAL,
    _HR_VERSION,
    _VERSION,
    _PLURAL,
    _CHART_PLURAL,
    _POLL_INTERVAL_S,
    _POLL_TIMEOUT_S,
    _api_message,
    chart_cr_name,
    is_flux_managed,
)

_APP_FINALIZER = "operatorkit.giantswarm.io/app-operator-app"
_CHART_FINALIZER = "operatorkit.giantswarm.io/chart-operator-chart"


def verify_migration(api: client.CustomObjectsApi, app: dict) -> list[str]:
    meta = app.get("metadata", {})
    name = meta.get("name", "")
    namespace = meta.get("namespace", "")

    try:
        hr = api.get_namespaced_custom_object(
            group=_HR_GROUP, version=_HR_VERSION, namespace=namespace, plural=_HR_PLURAL, name=name,
        )
    except ApiException as e:
        if e.status == 404:
            return [f"HelmRelease {namespace}/{name} not found — nothing to clean up (has `migrate` been run?)"]
        return [f"failed to fetch HelmRelease {namespace}/{name}: {_api_message(e)}"]

    failures = []

    conditions = (hr.get("status") or {}).get("conditions") or []
    ready = next((c for c in conditions if c.get("type") == "Ready"), None)
    if ready is None:
        failures.append(f"HelmRelease {namespace}/{name}: no Ready condition present")
    elif ready.get("status") != "True":
        detail = "; ".join(x for x in (ready.get("reason"), ready.get("message")) if x)
        suffix = f" ({detail})" if detail else ""
        failures.append(f"HelmRelease {namespace}/{name}: Ready condition is not True{suffix}")

    if (hr.get("spec") or {}).get("suspend"):
        failures.append(f"HelmRelease {namespace}/{name}: spec.suspend is true")

    observed_gen = (hr.get("status") or {}).get("observedGeneration")
    metadata_gen = (hr.get("metadata") or {}).get("generation")
    if observed_gen is not None and metadata_gen is not None and observed_gen != metadata_gen:
        failures.append(
            f"HelmRelease {namespace}/{name}: observedGeneration ({observed_gen}) does not match "
            f"metadata.generation ({metadata_gen}) — Ready describes an older revision than the "
            f"one gitops last pushed"
        )

    if is_flux_managed(app):
        if not is_flux_managed(hr):
            failures.append(
                f"HelmRelease {namespace}/{name}: missing {_FLUX_NAME_LABEL}/{_FLUX_NS_LABEL} labels — "
                f"gitops has not adopted this HelmRelease yet; commit the generated Flux resources to "
                f"your gitops repository and remove the App CR from it first"
            )
        app_labels = meta.get("labels") or {}
        if app_labels.get(_FLUX_RECONCILE_LABEL) != "disabled":
            failures.append(
                f"App {namespace}/{name}: missing {_FLUX_RECONCILE_LABEL}: disabled label — "
                f"Flux will re-apply the App CR after deletion"
            )

    return failures


def flux_cleanup_message(app: dict, context: str | None = None) -> str:
    meta = app.get("metadata", {})
    name = meta.get("name", "")
    namespace = meta.get("namespace", "")
    chart_name = chart_cr_name(app)

    cleanup_cmd = f"python main.py cleanup --name {name} --namespace {namespace}"
    if context:
        cleanup_cmd += f" --context {context}"

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
        f"Migration complete. This app is managed by Flux, so the App CR and Chart CR must not be "
        f"deleted until gitops has adopted the generated resources — deleting them beforehand would "
        f"cause the Kustomization to recreate them and undo the migration. Commit the generated Flux "
        f"resources to your gitops repository and remove the App CR from it, let Flux reconcile, then "
        f"run:\n\n"
        f"  {cleanup_cmd}\n\n"
        f"That command verifies the HelmRelease is ready and adopted by gitops before deleting "
        f"anything. If it is unavailable, here is the manual fallback:\n\n"
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
) -> list[str]:
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

    live_chart = None
    try:
        live_chart = chart_api.get_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=_CHART_NAMESPACE,
            plural=_CHART_PLURAL, name=chart_name,
        )
    except ApiException as e:
        if e.status != 404:
            raise MigratorError(
                f"failed to fetch Chart {_CHART_NAMESPACE}/{chart_name}: {_api_message(e)}"
            ) from e

    notes = []
    errors = []

    # Chart CR first
    if live_chart is None:
        notes.append(
            f"Chart {_CHART_NAMESPACE}/{chart_name} not found — skipped, nothing to delete."
        )
    else:
        try:
            _ensure_paused(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name, _CHART_PAUSED_ANNOTATION, live_chart)
            _remove_finalizer_and_delete(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name, _CHART_FINALIZER, live_chart)
            _poll_until_gone(chart_api, _GROUP, _VERSION, _CHART_NAMESPACE, _CHART_PLURAL, chart_name)
        except MigratorError as e:
            errors.append(e)

    # App CR second
    try:
        _ensure_paused(app_api, _GROUP, _VERSION, namespace, _PLURAL, name, _APP_PAUSED_ANNOTATION, live_app)
        _remove_finalizer_and_delete(app_api, _GROUP, _VERSION, namespace, _PLURAL, name, _APP_FINALIZER, live_app)
        _poll_until_gone(app_api, _GROUP, _VERSION, namespace, _PLURAL, name)
    except MigratorError as e:
        errors.append(e)

    if errors:
        raise MigratorError("; ".join(str(e) for e in errors))

    return notes
