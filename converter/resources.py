from collections import OrderedDict

from converter.values_from import calculate_values_from


def build_oci_repository(app: dict) -> OrderedDict:
    version = app["spec"].get("version")
    if not version:
        raise ValueError("spec.version is required but empty")
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
            ("url", f"oci://gsoci.azurecr.io/charts/giantswarm/{app['spec']['name']}"),
        ]))
    ])


_ANNOTATION_BLOCKLIST = {
    "chart-operator.giantswarm.io/force-helm-upgrade",
    "app-operator.giantswarm.io/paused",
}

_LABEL_BLOCKLIST = {
    "app-operator.giantswarm.io/version",
}


def build_helm_release(app: dict) -> OrderedDict:
    hr = OrderedDict([
        ("apiVersion", "helm.toolkit.fluxcd.io/v2"),
        ("kind", "HelmRelease"),
        ("metadata", OrderedDict([
            ("name", app["metadata"]["name"]),
            ("namespace", app["metadata"]["namespace"]),
        ])),
        ("spec", OrderedDict([
            ("chartRef", OrderedDict([
                ("kind", "OCIRepository"),
                ("name", app["metadata"]["name"]),
                ("namespace", app["metadata"]["namespace"]),
            ])),
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

    values_from = calculate_values_from(app)
    if values_from:
        hr["spec"]["valuesFrom"] = values_from

    return hr
