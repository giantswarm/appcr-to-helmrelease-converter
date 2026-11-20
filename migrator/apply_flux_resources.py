import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic import DynamicClient

from converter.release_chart import PREVENT_DELETION_LABEL

from . import MigrationStep, MigratorError, _api_message, _HR_GROUP, _HR_VERSION, _HR_PLURAL, _POLL_INTERVAL_S, _POLL_TIMEOUT_S

_SOURCE_META = {
    "OCIRepository": ("source.toolkit.fluxcd.io", "v1", "ocirepositories"),
    "HelmRepository": ("source.toolkit.fluxcd.io", "v1", "helmrepositories"),
}
_FIELD_MANAGER = "appcr-to-helmrelease-converter"
# Merge-patch fragment that clears the label the converter stamps on Cluster chart
# resources; the platform refuses a DELETE while it is set (ADR 0034). Clearing an
# absent label is a no-op, so it is sent for every app.
_UNPROTECT = {"metadata": {"labels": {PREVENT_DELETION_LABEL: None}}}


class ApplyFluxResources(MigrationStep):
    def __init__(self, api: client.CustomObjectsApi, docs: list):
        self._api = api
        self._source = docs[0]
        self._hr = docs[1]
        kind = self._source.get("kind", "")
        self._description = f"Apply {kind} and HelmRelease"

    @property
    def description(self) -> str:
        return self._description

    def apply(self) -> None:
        self._apply_resource(self._source)
        try:
            self._apply_resource(self._hr)
        except MigratorError:
            # Source was applied but HR failed — best-effort cleanup so the source
            # does not leak (MigrationRunner only stacks steps on full success).
            self._delete_resource(self._source)
            raise

    def _apply_resource(self, doc: dict) -> None:
        kind = doc.get("kind", "")
        meta = doc.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        group, version, plural = self._gvp(kind)
        path = f"/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}"
        try:
            dyn = DynamicClient(self._api.api_client)
            dyn.request("patch", path, body=doc,
                        field_manager=_FIELD_MANAGER, force_conflicts=True,
                        content_type="application/apply-patch+yaml")
        except ApiException as e:
            raise MigratorError(f"failed to apply {kind} {namespace}/{name}: {_api_message(e)}") from e

    def _delete_resource(self, doc: dict) -> None:
        kind = doc.get("kind", "")
        meta = doc.get("metadata", {})
        name = meta.get("name", "")
        namespace = meta.get("namespace", "")
        group, version, plural = self._gvp(kind)
        try:
            self._api.patch_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural,
                name=name, body=_UNPROTECT,
            )
        except ApiException:
            pass
        try:
            self._api.delete_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural,
                name=name,
            )
        except ApiException:
            pass

    def _gvp(self, kind: str) -> tuple[str, str, str]:
        if kind in _SOURCE_META:
            return _SOURCE_META[kind]
        return _HR_GROUP, _HR_VERSION, _HR_PLURAL

    def revert(self) -> None:
        errors = []
        hr_meta = self._hr.get("metadata", {})
        hr_name = hr_meta.get("name", "")
        hr_ns = hr_meta.get("namespace", "")
        src_meta = self._source.get("metadata", {})
        src_name = src_meta.get("name", "")
        src_ns = src_meta.get("namespace", "")
        src_kind = self._source.get("kind", "")
        src_group, src_version, src_plural = self._gvp(src_kind)

        # 1. Suspend HelmRelease via merge-patch so helm-controller stops reconciling,
        #    and clear the prevent-deletion label in the same patch so the delete is admitted
        try:
            self._api.patch_namespaced_custom_object(
                group=_HR_GROUP, version=_HR_VERSION, namespace=hr_ns, plural=_HR_PLURAL,
                name=hr_name, body={**_UNPROTECT, "spec": {"suspend": True}},
            )
        except ApiException as e:
            errors.append(MigratorError(
                f"failed to suspend HelmRelease {hr_ns}/{hr_name} and remove its "
                f"{PREVENT_DELETION_LABEL} label: {_api_message(e)}"
            ))

        # 2. Delete HelmRelease and confirm it is gone before touching the source
        hr_confirmed_deleted = False
        try:
            self._api.delete_namespaced_custom_object(
                group=_HR_GROUP, version=_HR_VERSION, namespace=hr_ns, plural=_HR_PLURAL,
                name=hr_name,
            )
        except ApiException as e:
            errors.append(MigratorError(f"failed to delete HelmRelease {hr_ns}/{hr_name}: {_api_message(e)}"))
        else:
            for _ in range(int(_POLL_TIMEOUT_S / _POLL_INTERVAL_S)):
                try:
                    self._api.get_namespaced_custom_object(
                        group=_HR_GROUP, version=_HR_VERSION, namespace=hr_ns, plural=_HR_PLURAL,
                        name=hr_name,
                    )
                except ApiException as e:
                    if e.status == 404:
                        hr_confirmed_deleted = True
                        break
                    errors.append(MigratorError(f"failed to poll HelmRelease {hr_ns}/{hr_name}: {_api_message(e)}"))
                    break
                time.sleep(_POLL_INTERVAL_S)
            else:
                errors.append(MigratorError(
                    f"timed out waiting for HelmRelease {hr_ns}/{hr_name} to be deleted; "
                    "check finalizers or delete manually"
                ))

        # 3. Delete source only once HelmRelease is confirmed gone
        if hr_confirmed_deleted:
            try:
                self._api.patch_namespaced_custom_object(
                    group=src_group, version=src_version, namespace=src_ns, plural=src_plural,
                    name=src_name, body=_UNPROTECT,
                )
            except ApiException as e:
                errors.append(MigratorError(
                    f"failed to remove {PREVENT_DELETION_LABEL} label from "
                    f"{src_kind} {src_ns}/{src_name}: {_api_message(e)}"
                ))
            try:
                self._api.delete_namespaced_custom_object(
                    group=src_group, version=src_version, namespace=src_ns, plural=src_plural,
                    name=src_name,
                )
            except ApiException as e:
                errors.append(MigratorError(f"failed to delete {src_kind} {src_ns}/{src_name}: {_api_message(e)}"))

        if errors:
            raise MigratorError("; ".join(str(e) for e in errors))
