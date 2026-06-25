import json
from abc import ABC, abstractmethod

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException


_GROUP = "application.giantswarm.io"
_VERSION = "v1alpha1"
_PLURAL = "apps"
_CHART_PLURAL = "charts"

_HR_GROUP = "helm.toolkit.fluxcd.io"
_HR_VERSION = "v2"
_HR_PLURAL = "helmreleases"
_CHART_NAMESPACE = "giantswarm"
_APP_PAUSED_ANNOTATION = "app-operator.giantswarm.io/paused"
_CHART_PAUSED_ANNOTATION = "chart-operator.giantswarm.io/paused"
_CLUSTER_LABEL = "giantswarm.io/cluster"
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 300

_FLUX_NAME_LABEL = "kustomize.toolkit.fluxcd.io/name"
_FLUX_NS_LABEL = "kustomize.toolkit.fluxcd.io/namespace"
_FLUX_RECONCILE_LABEL = "kustomize.toolkit.fluxcd.io/reconcile"


class MigratorError(Exception):
    pass


class MigrationStep(ABC):
    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def apply(self) -> None: ...

    @abstractmethod
    def revert(self) -> None: ...

    @property
    def skipped(self) -> bool:
        return False

    @property
    def revert_note(self) -> str | None:
        return None


class MigrationRunner:
    def __init__(self):
        self._stack = []
        self.revert_notes: list[str] = []

    def run(self, step: MigrationStep) -> None:
        step.apply()
        self._stack.append(step)
        if step.revert_note:
            self.revert_notes.append(step.revert_note)

    def revert_all(self) -> None:
        errors = []
        while self._stack:
            step = self._stack.pop()
            try:
                step.revert()
            except MigratorError as e:
                errors.append(e)
        if errors:
            raise MigratorError("; ".join(str(e) for e in errors))


def _api_message(e: ApiException) -> str:
    try:
        return json.loads(e.body)["message"]
    except (TypeError, ValueError, KeyError):
        return e.reason or str(e.status)


def chart_cr_name(app_dict: dict) -> str:
    meta = app_dict.get("metadata", {})
    name = meta.get("name", "")
    cluster_id = (meta.get("labels") or {}).get(_CLUSTER_LABEL, "")
    if cluster_id:
        name = name.removeprefix(f"{cluster_id}-")
        name = name.removesuffix(f"-{cluster_id}")
    return name


def core_client() -> client.CoreV1Api:
    return client.CoreV1Api()


def load_client(context: str | None) -> client.CustomObjectsApi:
    try:
        config.load_kube_config(context=context)
    except ConfigException as e:
        raise MigratorError(str(e)) from e
    return client.CustomObjectsApi()


def load_wc_client(core_api: client.CoreV1Api, secret_name: str, secret_namespace: str) -> client.CustomObjectsApi:
    try:
        secret = core_api.read_namespaced_secret(name=secret_name, namespace=secret_namespace)
    except ApiException as e:
        raise MigratorError(
            f"failed to fetch kubeconfig secret {secret_namespace}/{secret_name}: {_api_message(e)}"
        ) from e
    kubeconfig_bytes = (secret.data or {}).get("value")
    if not kubeconfig_bytes:
        raise MigratorError(
            f"kubeconfig secret {secret_namespace}/{secret_name} has no 'value' key"
        )
    import base64 as _base64
    import yaml as _yaml
    kubeconfig_dict = _yaml.safe_load(_base64.b64decode(kubeconfig_bytes))
    wc_conf = client.Configuration()
    config.load_kube_config_from_dict(kubeconfig_dict, client_configuration=wc_conf)
    return client.CustomObjectsApi(api_client=client.ApiClient(configuration=wc_conf))


# Step classes — imported last so the partial module satisfies their `from . import` references
from .apply_flux_resources import ApplyFluxResources  # noqa: E402
from .disable_flux_reconcile_app import DisableFluxReconcileApp  # noqa: E402
from .monitor_helm_release import MonitorHelmRelease  # noqa: E402
from .suspend_app import SuspendApp  # noqa: E402
from .suspend_chart import SuspendChart  # noqa: E402
