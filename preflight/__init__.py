class PreflightIssue(Exception):
    pass


class PreflightWarning(PreflightIssue):
    pass


class PreflightError(PreflightIssue):
    pass


def check_kube_config(app: dict) -> list[PreflightIssue]:
    kube_config = app.get("spec", {}).get("kubeConfig", {})
    if not kube_config or kube_config.get("inCluster"):
        return []
    secret_ns = kube_config.get("secret", {}).get("namespace")
    app_ns = app["metadata"]["namespace"]
    if secret_ns and secret_ns != app_ns:
        return [PreflightError(
            f'spec.kubeConfig.secret.namespace "{secret_ns}" does not match app namespace "{app_ns}";'
            " Flux requires the kubeconfig Secret to be in the same namespace as the HelmRelease"
        )]
    return []


def check_namespace_config(app: dict) -> list[PreflightIssue]:
    if app.get("spec", {}).get("namespaceConfig"):
        return [PreflightWarning(
            "spec.namespaceConfig dropped (no HelmRelease equivalent; target namespace already exists)"
        )]
    return []


def check_empty_values_from_names(app: dict) -> list[PreflightIssue]:
    issues = []
    spec = app.get("spec", {})
    _FIELDS = [
        ("config", "configMap", "spec.config.configMap"),
        ("config", "secret", "spec.config.secret"),
        ("userConfig", "configMap", "spec.userConfig.configMap"),
        ("userConfig", "secret", "spec.userConfig.secret"),
    ]
    for section, key, path in _FIELDS:
        ref = spec.get(section, {}).get(key, {})
        if ref and not ref.get("name"):
            issues.append(PreflightWarning(f"{path}.name is empty; skipping valuesFrom entry"))
    return issues


_CHECKS = [check_kube_config, check_namespace_config, check_empty_values_from_names]


def run_preflight(app: dict) -> list[PreflightIssue]:
    return [issue for check in _CHECKS for issue in check(app)]
