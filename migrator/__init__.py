import json
from abc import ABC, abstractmethod

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException


_GROUP = "application.giantswarm.io"
_VERSION = "v1alpha1"
_PLURAL = "apps"

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


class MigrationRunner:
    def __init__(self):
        self._stack = []

    def run(self, step: MigrationStep) -> None:
        step.apply()
        self._stack.append(step)

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


def load_client(context: str | None) -> client.CustomObjectsApi:
    try:
        config.load_kube_config(context=context)
    except ConfigException as e:
        raise MigratorError(str(e)) from e
    return client.CustomObjectsApi()


# Step classes — imported last so the partial module satisfies their `from . import` references
from .disable_flux_reconcile_app import DisableFluxReconcileApp  # noqa: E402
from .suspend_app import SuspendApp  # noqa: E402
