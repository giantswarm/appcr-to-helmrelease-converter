import json
from dataclasses import dataclass, field

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    app: dict
    catalog: dict
    referenced_configs: dict = field(default_factory=dict)
    dependency_helm_releases: dict = field(default_factory=dict)


def _api_message(e: ApiException) -> str:
    try:
        return json.loads(e.body)["message"]
    except (TypeError, ValueError, KeyError):
        return e.reason or str(e.status)


def fetch(name: str, namespace: str, context: str | None = None) -> FetchResult:
    try:
        config.load_kube_config(context=context)
    except ConfigException as e:
        raise FetchError(str(e)) from e
    api = client.CustomObjectsApi()

    try:
        app = api.get_namespaced_custom_object(
            group="application.giantswarm.io",
            version="v1alpha1",
            namespace=namespace,
            plural="apps",
            name=name,
        )
    except ApiException as e:
        raise FetchError(f"failed to fetch App {namespace}/{name}: {_api_message(e)}") from e

    catalog_name = app["spec"]["catalog"]
    catalog_namespace = app["spec"].get("catalogNamespace")

    namespaces = [catalog_namespace] if catalog_namespace else ["default", "giantswarm"]

    catalog = None
    for ns in namespaces:
        try:
            catalog = api.get_namespaced_custom_object(
                group="application.giantswarm.io",
                version="v1alpha1",
                namespace=ns,
                plural="catalogs",
                name=catalog_name,
            )
            break
        except ApiException as e:
            if e.status != 404:
                raise FetchError(f"failed to fetch Catalog {catalog_name}: {_api_message(e)}") from e
            if catalog_namespace:
                raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in {ns}") from e

    if catalog is None:
        raise FetchError(f"failed to fetch Catalog {catalog_name}: not found in default or giantswarm")

    core_api = client.CoreV1Api()
    referenced_configs = _fetch_referenced_configs(app, core_api)
    dependency_helm_releases = _fetch_dependency_helm_releases(api, app, namespace)
    return FetchResult(
        app=app,
        catalog=catalog,
        referenced_configs=referenced_configs,
        dependency_helm_releases=dependency_helm_releases,
    )


def _fetch_dependency_helm_releases(api, app: dict, namespace: str) -> dict:
    raw = (app.get("metadata") or {}).get("annotations", {}).get(
        "app-operator.giantswarm.io/depends-on", ""
    )
    names = [n.strip() for n in raw.split(",") if n.strip()]
    result = {}
    for name in names:
        try:
            result[name] = api.get_namespaced_custom_object(
                group="helm.toolkit.fluxcd.io",
                version="v2",
                namespace=namespace,
                plural="helmreleases",
                name=name,
            )
        except ApiException as e:
            if e.status == 404:
                result[name] = None
            else:
                raise FetchError(f"failed to fetch HelmRelease {namespace}/{name}: {_api_message(e)}") from e
    return result


def iter_refs(app: dict):
    app_ns = app["metadata"]["namespace"]
    spec = app.get("spec", {})

    for section, kind in [("config", "ConfigMap"), ("config", "Secret"),
                           ("userConfig", "ConfigMap"), ("userConfig", "Secret")]:
        sub_key = "configMap" if kind == "ConfigMap" else "secret"
        ref = spec.get(section, {}).get(sub_key, {})
        if ref and ref.get("name"):
            ns = ref.get("namespace") or app_ns
            yield kind, ref["name"], ns

    for entry in spec.get("extraConfigs", []):
        if entry.get("name"):
            kind = entry.get("kind", "ConfigMap")
            kind = kind[0].upper() + kind[1:]
            ns = entry.get("namespace") or app_ns
            yield kind, entry["name"], ns


def _fetch_one(core_api, kind: str, name: str, ns: str) -> dict | None:
    try:
        if kind == "ConfigMap":
            return core_api.read_namespaced_config_map(name=name, namespace=ns).to_dict()
        return core_api.read_namespaced_secret(name=name, namespace=ns).to_dict()
    except ApiException as e:
        if e.status == 404:
            return None
        raise FetchError(f"failed to fetch {kind} {ns}/{name}: {_api_message(e)}") from e


def _fetch_referenced_configs(app: dict, core_api) -> dict:
    refs = {}
    for kind, name, ns in iter_refs(app):
        key = (kind, name, ns)
        if key not in refs:
            refs[key] = _fetch_one(core_api, kind, name, ns)
    return refs
