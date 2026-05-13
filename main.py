import fileinput
import sys
from collections import OrderedDict, namedtuple
from typing import Any, Optional, List

import yaml
from yaml.resolver import BaseResolver


ReferenceWithPriority = namedtuple("ReferenceWithPriority", ["reference", "priority"])


def to_reference_with_priority(reference: dict, kind: str, default_priority: int) -> Optional[ReferenceWithPriority]:
    if reference:
        return ReferenceWithPriority(
            priority=reference.get("priority", default_priority),
            reference=OrderedDict([
                ("kind", kind[0].upper() + kind[1:]),
                ("name", reference.get("name", "")),
                ("valuesKey", "configmap-values.yaml" if kind.lower() == "configmap" else "secret-values.yaml"),
              ])
        )

    return None


def calculate_values_from(app: dict) -> list[OrderedDict[Any, Any]]:
    cluster_config_map = app["spec"].get("config", {}).get("configMap", {})
    cluster_secret  = app["spec"].get("config", {}).get("secret", {})

    user_config_map = app["spec"].get("userConfig", {}).get("configMap", {})
    user_secret  = app["spec"].get("userConfig", {}).get("secret", {})

    extra_configs  = app["spec"].get("extraConfigs", [])

    intermediate_result = []

    result = to_reference_with_priority(cluster_config_map, "ConfigMap", 50)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(cluster_secret, "Secret", 50)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(user_config_map, "ConfigMap", 100)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(user_secret, "Secret", 100)
    if result: intermediate_result.append(result)

    for extra_config in extra_configs:
        result = to_reference_with_priority(
            extra_config,
            extra_config.get("kind", "ConfigMap"),
            25
        )
        if result: intermediate_result.append(result)

    intermediate_result.sort(key=lambda x: x.priority, reverse=False)
    intermediate_result.sort(key=lambda x: x.reference["kind"], reverse=False)

    return [item.reference for item in intermediate_result]


def convert(content: str) -> List[OrderedDict[Any, Any]]:
    app = yaml.safe_load(content)

    oci_repository = OrderedDict([
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
                ("semver", "x.x.x"),
                # ("tag", app["spec"]["version"]),
            ])),
            ("url", f"oci://gsoci.azurecr.io/charts/giantswarm/{app["spec"]["name"]}"),
        ]))
    ])

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
            # ("suspend", True),
            ("storageNamespace", app["spec"]["namespace"]),
            ("targetNamespace", app["spec"]["namespace"]),
            ("timeout", "10m"),
            ("upgrade", OrderedDict([
                ("remediation", OrderedDict([
                    ("remediateLastFailure", True),
                    ("retries", 10),
                    ("strategy", "rollback"),
                ]))
            ]))
        ]))
    ])

    annotations = OrderedDict()
    for key, value in app["metadata"].get('annotations', {}).items():
        if key not in [
            'chart-operator.giantswarm.io/force-helm-upgrade',
            'app-operator.giantswarm.io/paused'
        ]:
            annotations[key] = value

    if len(annotations.keys()) > 0:
        hr["metadata"]["annotations"] = annotations

    labels = OrderedDict()
    for key, value in app["metadata"].get('labels', {}).items():
        if key not in [
            'app-operator.giantswarm.io/version'
        ]:
            labels[key] = value

    if len(labels.keys()) > 0:
        hr["metadata"]["labels"] = labels

    values_from = calculate_values_from(app)

    if len(values_from) > 0:
        hr["spec"]["valuesFrom"] = values_from

    return [oci_repository, hr]


def main() -> None:
    content = ""
    for line in fileinput.input():
        content += line

    result = convert(content)

    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping(
            BaseResolver.DEFAULT_MAPPING_TAG,
            data.items()
        )

    yaml.add_representer(OrderedDict, ordered_dict_representer)

    print("---")
    yaml.dump_all(result, sys.stdout, sort_keys=False, default_flow_style=False)


if __name__ == '__main__':
    main()
