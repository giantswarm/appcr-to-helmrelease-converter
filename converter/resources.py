from collections import OrderedDict

from converter.values_from import calculate_values_from


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


def build_helm_release_and_oci_repo(app: dict, catalog: dict) -> list:
    return [build_oci_repository(app, catalog), build_helm_release(app)]


def build_helm_release_and_helm_repo(app: dict, catalog: dict) -> list:
    return [_build_helm_repository(app, catalog), _build_helm_release_helm(app)]


def build_oci_repository(app: dict, catalog: dict) -> OrderedDict:
    version = app["spec"].get("version")
    if not version:
        raise ValueError("spec.version is required but empty")
    url_base = _oci_url_from_catalog(catalog)
    url = f"{url_base.rstrip('/')}/{app['spec']['name']}"
    return OrderedDict([
        ("apiVersion", "source.toolkit.fluxcd.io/v1beta2"),
        ("kind", "OCIRepository"),
        ("metadata", OrderedDict([
            ("name", app["metadata"]["name"]),
            ("namespace", app["metadata"]["namespace"]),
        ])),
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
        ("metadata", OrderedDict([
            ("name", app["metadata"]["name"]),
            ("namespace", app["metadata"]["namespace"]),
        ])),
        ("spec", OrderedDict([
            ("interval", "10m"),
            ("url", url),
        ])),
    ])


_SERVICE_ACCOUNT_EXCLUDED_NAMESPACES = {"giantswarm", "flux-giantswarm", "monitoring"}


_ANNOTATION_BLOCKLIST = {
    "chart-operator.giantswarm.io/force-helm-upgrade",
    "app-operator.giantswarm.io/paused",
}

_LABEL_BLOCKLIST = {
    "app-operator.giantswarm.io/version",
}


def _build_helm_release_common(app: dict) -> OrderedDict:
    hr = OrderedDict([
        ("apiVersion", "helm.toolkit.fluxcd.io/v2"),
        ("kind", "HelmRelease"),
        ("metadata", OrderedDict([
            ("name", app["metadata"]["name"]),
            ("namespace", app["metadata"]["namespace"]),
        ])),
        ("spec", OrderedDict([
            ("install", OrderedDict([
                ("remediation", OrderedDict([
                    ("remediateLastFailure", False),
                    ("retries", 10),
                ]))
            ])),
            ("interval", "5m"),
            ("releaseName", app["metadata"]["name"]),
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
        ]))
    ])

    annotations = OrderedDict(
        (k, v) for k, v in app["metadata"].get("annotations", {}).items()
        if k not in _ANNOTATION_BLOCKLIST
    )
    if annotations:
        hr["metadata"]["annotations"] = annotations

    labels = OrderedDict(
        (k, v) for k, v in app["metadata"].get("labels", {}).items()
        if k not in _LABEL_BLOCKLIST
    )
    if labels:
        hr["metadata"]["labels"] = labels

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

    values_from = calculate_values_from(app)
    if values_from:
        hr["spec"]["valuesFrom"] = values_from

    return hr


def build_helm_release(app: dict) -> OrderedDict:
    hr = _build_helm_release_common(app)
    hr["spec"]["chartRef"] = OrderedDict([
        ("kind", "OCIRepository"),
        ("name", app["metadata"]["name"]),
        ("namespace", app["metadata"]["namespace"]),
    ])
    return hr


def _build_helm_release_helm(app: dict) -> OrderedDict:
    version = app["spec"].get("version")
    if not version:
        raise ValueError("spec.version is required but empty")
    hr = _build_helm_release_common(app)
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
