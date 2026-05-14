from collections import OrderedDict, namedtuple
from typing import Any, Optional

ReferenceWithPriority = namedtuple("ReferenceWithPriority", ["reference", "priority"])


def to_reference_with_priority(reference: dict, kind: str, default_priority: int) -> Optional[ReferenceWithPriority]:
    if reference and reference.get("name"):
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
    cluster_secret = app["spec"].get("config", {}).get("secret", {})

    user_config_map = app["spec"].get("userConfig", {}).get("configMap", {})
    user_secret = app["spec"].get("userConfig", {}).get("secret", {})

    extra_configs = app["spec"].get("extraConfigs", [])

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
