import base64
from dataclasses import dataclass

import yaml
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from . import MigratorError, _HR_GROUP, _HR_PLURAL, _HR_VERSION, _api_message

RELEASE_VERSION_VALUE = "global.release.version"

_DEFAULT_VALUES_KEY = "values.yaml"


@dataclass(frozen=True)
class ValuesSource:
    kind: str
    name: str
    namespace: str
    key: str

    def __str__(self) -> str:
        return f"{self.kind} {self.namespace}/{self.name} (key {self.key})"


def get_helm_release(api: client.CustomObjectsApi, app: dict) -> dict:
    meta = app.get("metadata") or {}
    name, namespace = meta.get("name", ""), meta.get("namespace", "")
    try:
        return api.get_namespaced_custom_object(
            group=_HR_GROUP, version=_HR_VERSION, namespace=namespace, plural=_HR_PLURAL, name=name,
        )
    except ApiException as e:
        raise MigratorError(f"failed to fetch HelmRelease {namespace}/{name}: {_api_message(e)}") from e


def _read(core_api: client.CoreV1Api, kind: str, name: str, namespace: str):
    try:
        if kind == "Secret":
            return core_api.read_namespaced_secret(name=name, namespace=namespace)
        return core_api.read_namespaced_config_map(name=name, namespace=namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise MigratorError(f"failed to fetch {kind} {namespace}/{name}: {_api_message(e)}") from e


def _decode(kind: str, raw: str) -> str:
    return base64.b64decode(raw).decode() if kind == "Secret" else raw


def _encode(kind: str, text: str) -> str:
    return base64.b64encode(text.encode()).decode() if kind == "Secret" else text


def _load_values(source: ValuesSource, raw: str) -> dict:
    try:
        values = yaml.safe_load(_decode(source.kind, raw))
    except yaml.YAMLError as e:
        raise MigratorError(f"{source} does not hold valid YAML: {e}") from e
    return values if isinstance(values, dict) else {}


def _release_block(values: dict) -> dict | None:
    glob = values.get("global")
    release = glob.get("release") if isinstance(glob, dict) else None
    return release if isinstance(release, dict) else None


def find_release_version_sources(core_api: client.CoreV1Api, helm_release: dict) -> list[ValuesSource]:
    namespace = (helm_release.get("metadata") or {}).get("namespace", "")
    found = []
    for entry in (helm_release.get("spec") or {}).get("valuesFrom") or []:
        if entry.get("targetPath"):
            continue
        source = ValuesSource(
            kind=entry.get("kind", "ConfigMap"),
            name=entry.get("name", ""),
            namespace=namespace,
            key=entry.get("valuesKey") or _DEFAULT_VALUES_KEY,
        )
        obj = _read(core_api, source.kind, source.name, source.namespace)
        raw = (obj.data or {}).get(source.key) if obj is not None else None
        if raw is None:
            continue
        release = _release_block(_load_values(source, raw))
        if release is not None and "version" in release:
            found.append(source)
    return found


def remove_release_version(core_api: client.CoreV1Api, source: ValuesSource) -> None:
    obj = _read(core_api, source.kind, source.name, source.namespace)
    raw = (obj.data or {}).get(source.key) if obj is not None else None
    if raw is None:
        return
    values = _load_values(source, raw)
    release = _release_block(values)
    if release is None or "version" not in release:
        return
    del release["version"]
    if not release:
        del values["global"]["release"]
    if not values["global"]:
        del values["global"]
    text = yaml.safe_dump(values, sort_keys=False, default_flow_style=False) if values else ""
    body = {"data": {source.key: _encode(source.kind, text)}}
    try:
        if source.kind == "Secret":
            core_api.patch_namespaced_secret(name=source.name, namespace=source.namespace, body=body)
        else:
            core_api.patch_namespaced_config_map(name=source.name, namespace=source.namespace, body=body)
    except ApiException as e:
        raise MigratorError(
            f"failed to remove {RELEASE_VERSION_VALUE} from {source}: {_api_message(e)}"
        ) from e
