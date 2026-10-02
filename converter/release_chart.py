from copy import deepcopy

# Mirrors SupportedProviders in giantswarm/releases sdk/api/v1alpha1/types.go.
# Hardcoded on purpose — see ADR 0032.
CLUSTER_CHART_PROVIDERS = (
    "aws",
    "azure",
    "vsphere",
    "cloud-director",
    "eks",
    "proxmox",
    "aks",
)

RELEASE_CHART_REGISTRY_HOST = "gsoci.azurecr.io"
RELEASE_CHART_REPOSITORY_PREFIX = "charts/giantswarm"

RELEASE_VERSION_LABEL = "release.giantswarm.io/version"

_CLUSTER_CHART_PREFIX = "cluster-"


def cluster_chart_provider(app: dict) -> str | None:
    chart = (app.get("spec") or {}).get("name") or ""
    if not chart.startswith(_CLUSTER_CHART_PREFIX):
        return None
    provider = chart.removeprefix(_CLUSTER_CHART_PREFIX)
    return provider if provider in CLUSTER_CHART_PROVIDERS else None


def cluster_chart_name(provider: str) -> str:
    return f"{_CLUSTER_CHART_PREFIX}{provider}"


def release_chart_name(provider: str) -> str:
    return f"release-{provider}"


def release_cr_name(provider: str, release_version: str) -> str:
    return f"{provider}-{release_version}"


def release_version_from_cluster(cluster: dict) -> str | None:
    labels = (cluster.get("metadata") or {}).get("labels") or {}
    version = labels.get(RELEASE_VERSION_LABEL)
    return version.removeprefix("v") if version else None


def pinned_cluster_chart_version(release_cr: dict, provider: str) -> str | None:
    chart = cluster_chart_name(provider)
    for component in (release_cr.get("spec") or {}).get("components") or []:
        if component.get("name") == chart:
            return component.get("version")
    return None


def substitute_release_chart(app: dict, provider: str, release_version: str) -> dict:
    app = deepcopy(app)
    app["spec"]["name"] = release_chart_name(provider)
    app["spec"]["version"] = release_version
    return app
