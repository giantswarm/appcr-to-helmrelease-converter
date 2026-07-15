from collections import OrderedDict, namedtuple
from typing import Any, Optional

ReferenceWithPriority = namedtuple("ReferenceWithPriority", ["reference", "priority"])

PSP_REMOVAL_PATCH_PREFIX = "psp-removal-patch"


def is_psp_removal_patch(entry: dict, kind: str, app_namespace: str) -> bool:
    if kind.lower() != "configmap" or not entry.get("name", "").startswith(PSP_REMOVAL_PATCH_PREFIX):
        return False
    return (entry.get("namespace") or app_namespace) == app_namespace


def _default_values_key(kind: str) -> str:
    return "configmap-values.yaml" if kind.lower() == "configmap" else "secret-values.yaml"


def to_reference_with_priority(reference: dict, kind: str, default_priority: int, resolution=None) -> Optional[ReferenceWithPriority]:
    if reference and reference.get("name"):
        name = reference.get("name", "")
        canonical_kind = kind[0].upper() + kind[1:]
        if resolution is not None and (canonical_kind, name) in resolution.key_overrides:
            override = resolution.key_overrides[(canonical_kind, name)]
            values_key_items = [] if (override is None or override == "values.yaml") else [("valuesKey", override)]
        else:
            values_key_items = [("valuesKey", _default_values_key(kind))]
        return ReferenceWithPriority(
            priority=reference.get("priority", default_priority),
            reference=OrderedDict([
                ("kind", canonical_kind),
                ("name", name),
                *values_key_items,
            ])
        )
    return None


def calculate_values_from(app: dict, resolution=None) -> list[OrderedDict[Any, Any]]:
    app_namespace = (app.get("metadata") or {}).get("namespace", "")

    cluster_config_map = app["spec"].get("config", {}).get("configMap", {})
    cluster_secret = app["spec"].get("config", {}).get("secret", {})

    user_config_map = app["spec"].get("userConfig", {}).get("configMap", {})
    user_secret = app["spec"].get("userConfig", {}).get("secret", {})

    extra_configs = app["spec"].get("extraConfigs", [])

    intermediate_result = []

    result = to_reference_with_priority(cluster_config_map, "ConfigMap", 50, resolution)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(cluster_secret, "Secret", 50, resolution)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(user_config_map, "ConfigMap", 100, resolution)
    if result: intermediate_result.append(result)

    result = to_reference_with_priority(user_secret, "Secret", 100, resolution)
    if result: intermediate_result.append(result)

    for extra_config in extra_configs:
        kind = extra_config.get("kind", "ConfigMap")
        if is_psp_removal_patch(extra_config, kind, app_namespace):
            continue
        result = to_reference_with_priority(extra_config, kind, 25, resolution)
        if result: intermediate_result.append(result)

    intermediate_result.sort(key=lambda x: x.priority, reverse=False)
    intermediate_result.sort(key=lambda x: x.reference["kind"], reverse=False)

    return [item.reference for item in intermediate_result]
