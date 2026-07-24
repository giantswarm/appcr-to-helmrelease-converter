from collections import OrderedDict

from converter.values_from import calculate_values_from

_CLUSTER_LABEL = "giantswarm.io/cluster"


def _release_name(app: dict) -> str:
    name = app["metadata"]["name"]
    cluster_id = (app["metadata"].get("labels") or {}).get(_CLUSTER_LABEL, "")
    if cluster_id:
        name = name.removeprefix(f"{cluster_id}-")
        name = name.removesuffix(f"-{cluster_id}")
    return name


def _oci_url_from_catalog(catalog: dict) -> str:
    for repo in (catalog.get("spec") or {}).get("repositories") or []:
        if repo.get("type") == "oci":
            return repo["URL"]
    storage = (catalog.get("spec") or {}).get("storage") or {}
    if storage.get("type") == "oci":
        return storage["URL"]
    raise ValueError("catalog contains no oci repository")


def _helm_url_from_catalog(catalog: dict) -> str:
    for repo in (catalog.get("spec") or {}).get("repositories") or []:
        if repo.get("type") == "helm":
            return repo["URL"]
    storage = (catalog.get("spec") or {}).get("storage") or {}
    if storage.get("type") == "helm":
        return storage["URL"]
    raise ValueError("catalog contains no helm repository")


def build_helm_release_and_oci_repo(app: dict, catalog: dict, resolution=None) -> list:
    return [build_oci_repository(app, catalog), build_helm_release(app, resolution)]


def build_helm_release_and_helm_repo(app: dict, catalog: dict, resolution=None) -> list:
    return [_build_helm_repository(app, catalog), _build_helm_release_helm(app, resolution)]


def build_oci_repository(app: dict, catalog: dict) -> OrderedDict:
    version = app["spec"].get("version")
    if not version:
        raise ValueError("spec.version is required but empty")
    url_base = _oci_url_from_catalog(catalog)
    url = f"{url_base.rstrip('/')}/{app['spec']['name']}"
    return OrderedDict([
        ("apiVersion", "source.toolkit.fluxcd.io/v1"),
        ("kind", "OCIRepository"),
        ("metadata", _filtered_metadata(app)),
        ("spec", OrderedDict([
            ("interval", "10m"),
            ("provider", "generic"),
            ("ref", OrderedDict([
                ("tag", version),
            ])),
            ("url", url),
        ]))
    ])


def _build_helm_repository(app: dict, catalog: dict) -> OrderedDict:
    url = _helm_url_from_catalog(catalog)
    return OrderedDict([
        ("apiVersion", "source.toolkit.fluxcd.io/v1"),
        ("kind", "HelmRepository"),
        ("metadata", _filtered_metadata(app)),
        ("spec", OrderedDict([
            ("interval", "10m"),
            ("url", url),
        ])),
    ])


_SERVICE_ACCOUNT_EXCLUDED_NAMESPACES = {"giantswarm", "flux-giantswarm", "monitoring"}


_DEPENDS_ON_ANNOTATION = "app-operator.giantswarm.io/depends-on"

_ANNOTATION_BLOCKLIST = {
    _DEPENDS_ON_ANNOTATION,
    "app-operator.giantswarm.io/depends-on-helmrelease",
    "app-operator.giantswarm.io/latest-configmap-version",
    "app-operator.giantswarm.io/latest-secret-version",
    "app-operator.giantswarm.io/paused",
    "app-operator.giantswarm.io/trigger-reconciliation",
    "chart-operator.giantswarm.io/force-helm-upgrade",
}

_LABEL_BLOCKLIST = {
    "app-operator.giantswarm.io/version",
    "policy.giantswarm.io/psp-status",
}


def _filtered_metadata(app: dict) -> OrderedDict:
    meta = OrderedDict([
        ("name", app["metadata"]["name"]),
        ("namespace", app["metadata"]["namespace"]),
    ])

    annotations = OrderedDict(
        (k, v) for k, v in (app["metadata"].get("annotations") or {}).items()
        if k not in _ANNOTATION_BLOCKLIST and "fluxcd.io/" not in k
    )
    if annotations:
        meta["annotations"] = annotations

    labels = OrderedDict(
        (k, v) for k, v in (app["metadata"].get("labels") or {}).items()
        if k not in _LABEL_BLOCKLIST and "fluxcd.io/" not in k
    )
    if labels:
        meta["labels"] = labels

    return meta


def _build_helm_release_common(app: dict, resolution=None) -> OrderedDict:
    depends_on_raw = (app["metadata"].get("annotations") or {}).get(_DEPENDS_ON_ANNOTATION) or ""
    depends_on = [n.strip() for n in depends_on_raw.split(",") if n.strip()]

    spec_items = []
    if depends_on:
        spec_items.append(("dependsOn", [{"name": n} for n in depends_on]))
    spec_items += [
        ("install", OrderedDict([
            ("remediation", OrderedDict([
                ("remediateLastFailure", False),
                ("retries", 10),
            ]))
        ])),
        ("interval", "5m"),
        ("releaseName", _release_name(app)),
        ("storageNamespace", app["spec"]["namespace"]),
        ("targetNamespace", app["spec"]["namespace"]),
        ("timeout", "10m"),
        ("upgrade", OrderedDict([
            ("remediation", OrderedDict([
                ("remediateLastFailure", True),
                ("retries", 10),
                ("strategy", "rollback"),
            ]))
        ])),
    ]

    hr = OrderedDict([
        ("apiVersion", "helm.toolkit.fluxcd.io/v2"),
        ("kind", "HelmRelease"),
        ("metadata", _filtered_metadata(app)),
        ("spec", OrderedDict(spec_items)),
    ])

    kube_config = app["spec"].get("kubeConfig", {})
    is_remote = kube_config and not kube_config.get("inCluster")
    if is_remote:
        hr["spec"]["kubeConfig"] = OrderedDict([
            ("secretRef", OrderedDict([
                ("name", kube_config["secret"]["name"]),
            ]))
        ])
    elif app["spec"]["namespace"] not in _SERVICE_ACCOUNT_EXCLUDED_NAMESPACES:
        hr["spec"]["serviceAccountName"] = "automation"

    values_from = calculate_values_from(app, resolution)
    if values_from:
        hr["spec"]["valuesFrom"] = values_from

    return hr


def build_helm_release(app: dict, resolution=None) -> OrderedDict:
    hr = _build_helm_release_common(app, resolution)
    hr["spec"]["chartRef"] = OrderedDict([
        ("kind", "OCIRepository"),
        ("name", app["metadata"]["name"]),
        ("namespace", app["metadata"]["namespace"]),
    ])
    return hr


def _build_helm_release_helm(app: dict, resolution=None) -> OrderedDict:
    version = app["spec"].get("version")
    if not version:
        raise ValueError("spec.version is required but empty")
    hr = _build_helm_release_common(app, resolution)
    hr["spec"]["chart"] = OrderedDict([
        ("spec", OrderedDict([
            ("chart", app["spec"]["name"]),
            ("sourceRef", OrderedDict([
                ("kind", "HelmRepository"),
                ("name", app["metadata"]["name"]),
                ("namespace", app["metadata"]["namespace"]),
            ])),
            ("version", version),
        ])),
    ])
    return hr
