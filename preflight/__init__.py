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


def check_missing_referenced_configs(app: dict, catalog: dict, referenced_configs: dict) -> list[PreflightIssue]:
    return [
        PreflightError(f"{kind} \"{name}\" not found in namespace \"{ns}\"")
        for (kind, name, ns), resource in referenced_configs.items()
        if resource is None
    ]


def check_empty_referenced_configs(app: dict, catalog: dict, referenced_configs: dict) -> list[PreflightIssue]:
    return [
        PreflightError(f"{kind} \"{name}\" has no data keys; cannot determine valuesKey")
        for (kind, name, ns), resource in referenced_configs.items()
        if resource is not None and not (resource.get("data") or {})
    ]


def check_cross_namespace_refs(app: dict, catalog: dict, referenced_configs: dict) -> list[PreflightIssue]:
    app_ns = app.get("metadata", {}).get("namespace", "")
    return [
        PreflightError(
            f"{kind} \"{name}\" is in namespace \"{ns}\" but app is in \"{app_ns}\"; "
            "Flux does not support cross-namespace valuesFrom references"
        )
        for (kind, name, ns), resource in referenced_configs.items()
        if ns != app_ns and resource is not None
    ]


_CHECKS = [
    check_kube_config,
    check_namespace_config,
    check_empty_values_from_names,
    check_multiple_helm_repos,
]

_REFS_CHECKS = [
    check_cross_namespace_refs,
    check_missing_referenced_configs,
    check_empty_referenced_configs,
]


def run_preflight(app: dict, catalog: dict, referenced_configs: dict | None = None) -> list[PreflightIssue]:
    refs = referenced_configs or {}
    return (
        [issue for check in _CHECKS for issue in check(app, catalog)]
        + [issue for check in _REFS_CHECKS for issue in check(app, catalog, refs)]
    )
