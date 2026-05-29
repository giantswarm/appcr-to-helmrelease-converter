class PreflightIssue(Exception):
    pass


class PreflightWarning(PreflightIssue):
    def display(self) -> str:
        return f"⚠️  {self}"


class PreflightError(PreflightIssue):
    def display(self) -> str:
        return f"❌ {self}"


def check_kube_config(app: dict, catalog: dict) -> list[PreflightIssue]:
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


def check_namespace_config(app: dict, catalog: dict) -> list[PreflightIssue]:
    if app.get("spec", {}).get("namespaceConfig"):
        return [PreflightWarning(
            "spec.namespaceConfig dropped (no HelmRelease equivalent; target namespace already exists)"
        )]
    return []


def check_empty_values_from_names(app: dict, catalog: dict) -> list[PreflightIssue]:
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


def check_multiple_helm_repos(app: dict, catalog: dict) -> list[PreflightIssue]:
    count = sum(
        1 for repo in (catalog.get("spec") or {}).get("repositories") or []
        if repo.get("type") == "helm"
    )
    if count > 1:
        return [PreflightWarning("catalog has multiple helm repositories; using the first")]
    return []


_CHECKS = [
    check_kube_config,
    check_namespace_config,
    check_empty_values_from_names,
    check_multiple_helm_repos,
]


def run_preflight(app: dict, catalog: dict) -> list[PreflightIssue]:
    return [issue for check in _CHECKS for issue in check(app, catalog)]
