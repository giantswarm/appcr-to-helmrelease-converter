import json
from dataclasses import dataclass, field

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from converter.release_chart import (
    RELEASE_CHART_REGISTRY_HOST,
    RELEASE_CHART_REPOSITORY_PREFIX,
    cluster_chart_provider,
    release_chart_name,
    release_cr_name,
    release_version_from_cluster,
)
from converter.resources import release_name
from fetcher.registry import RegistryError, oci_tag_exists


_DEPENDS_ON_ANNOTATION = "app-operator.giantswarm.io/depends-on"

_CLUSTER_GROUP = "cluster.x-k8s.io"
_CLUSTER_VERSIONS = ("v1beta2", "v1beta1")
_HELM_INSTANCE_LABEL = "app.kubernetes.io/instance"
_RELEASE_GROUP = "release.giantswarm.io"
_RELEASE_VERSION = "v1alpha1"


class FetchError(Exception):
    pass


@dataclass
class ReleaseChartFacts:
    provider: str
    clusters: list = field(default_factory=list)
    release_version: str | None = None
    release_cr: dict | None = None
    published: bool | None = None


@dataclass
class FetchResult:
    app: dict
    catalog: dict
    referenced_configs: dict = field(default_factory=dict)
    dependency_helm_releases: dict = field(default_factory=dict)
    pull_secret: dict | None = None
    release_chart: ReleaseChartFacts | None = None


def _api_message(e: ApiException) -> str:
    try:
        return json.loads(e.body)["message"]
    except (TypeError, ValueError, KeyError):
        return e.reason or str(e.status)


def fetch(
    name: str, namespace: str, context: str | None = None, pull_secret: str | None = None
) -> FetchResult:
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
    pull_secret_obj = (
        _fetch_one(core_api, "Secret", pull_secret, namespace) if pull_secret else None
    )
    provider = cluster_chart_provider(app)
    release_chart = _fetch_release_chart_facts(api, app, provider) if provider else None
    return FetchResult(
        app=app,
        catalog=catalog,
        referenced_configs=referenced_configs,
        dependency_helm_releases=dependency_helm_releases,
        pull_secret=pull_secret_obj,
        release_chart=release_chart,
    )


def _list_clusters(api, namespace: str, helm_release: str) -> list:
    selector = f"{_HELM_INSTANCE_LABEL}={helm_release}"
    for version in _CLUSTER_VERSIONS:
        try:
            result = api.list_namespaced_custom_object(
                group=_CLUSTER_GROUP,
                version=version,
                namespace=namespace,
                plural="clusters",
                label_selector=selector,
            )
        except ApiException as e:
            if e.status == 404:
                continue
            raise FetchError(f"failed to list Clusters in {namespace}: {_api_message(e)}") from e
        return result.get("items") or []
    raise FetchError(
        f"failed to list Clusters in {namespace}: {_CLUSTER_GROUP} "
        f"{'/'.join(_CLUSTER_VERSIONS)} is not served by this cluster"
    )


def _get_release_cr(api, name: str) -> dict | None:
    try:
        return api.get_cluster_custom_object(
            group=_RELEASE_GROUP, version=_RELEASE_VERSION, plural="releases", name=name,
        )
    except ApiException as e:
        if e.status == 404:
            return None
        raise FetchError(f"failed to fetch Release {name}: {_api_message(e)}") from e


def _fetch_release_chart_facts(api, app: dict, provider: str) -> ReleaseChartFacts:
    facts = ReleaseChartFacts(
        provider=provider,
        clusters=_list_clusters(api, app["spec"]["namespace"], release_name(app)),
    )
    if len(facts.clusters) != 1:
        return facts
    facts.release_version = release_version_from_cluster(facts.clusters[0])
    if not facts.release_version:
        return facts
    facts.release_cr = _get_release_cr(api, release_cr_name(provider, facts.release_version))
    if facts.release_cr is None:
        return facts
    repository = f"{RELEASE_CHART_REPOSITORY_PREFIX}/{release_chart_name(provider)}"
    try:
        facts.published = oci_tag_exists(RELEASE_CHART_REGISTRY_HOST, repository, facts.release_version)
    except RegistryError as e:
        raise FetchError(str(e)) from e
    return facts


def _fetch_dependency_helm_releases(api, app: dict, namespace: str) -> dict:
    raw = ((app.get("metadata") or {}).get("annotations") or {}).get(_DEPENDS_ON_ANNOTATION) or ""
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
