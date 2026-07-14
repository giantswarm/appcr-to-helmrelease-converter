from converter.values_from import is_psp_removal_patch


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


def check_oci_fallback(app: dict, catalog: dict) -> list[PreflightIssue]:
    spec = catalog.get("spec") or {}
    has_oci = any(r.get("type") == "oci" for r in (spec.get("repositories") or [])) \
        or (spec.get("storage") or {}).get("type") == "oci"
    if not has_oci:
        return [PreflightWarning(
            "catalog has no OCI repository; conversion will use HelmRepository (deprecated by Flux — "
            "no new features; OCI is the preferred source type)"
        )]
    return []


def check_flux_managed(app: dict, catalog: dict) -> list[PreflightIssue]:
    labels = (app.get("metadata") or {}).get("labels") or {}
    if "kustomize.toolkit.fluxcd.io/name" in labels and "kustomize.toolkit.fluxcd.io/namespace" in labels:
        return [PreflightWarning(
            "App CR is managed by Flux; after migration add the converted HelmRelease and source resource "
            "to your gitops repository and remove the App CR from it. "
            "Remove finalizer operatorkit.giantswarm.io/app-operator-app from the App CR "
            "and operatorkit.giantswarm.io/chart-operator-chart from the Chart CR manually."
        )]
    return []


def check_psp_removal_patch(app: dict, catalog: dict) -> list[PreflightIssue]:
    app_namespace = app.get("metadata", {}).get("namespace", "")
    extra_configs = app.get("spec", {}).get("extraConfigs", [])
    for entry in extra_configs:
        kind = entry.get("kind", "ConfigMap")
        if is_psp_removal_patch(entry, kind, app_namespace):
            name = entry["name"]
            return [PreflightWarning(
                f'extraConfigs contains "{name}" (ConfigMap); this legacy PodSecurityPolicy '
                "patch is always dropped — PodSecurityPolicies were removed in Kubernetes 1.25. If the chart "
                "still errors without it, upgrade the chart instead of restoring this entry."
            )]
    return []


def check_multiple_helm_repos(app: dict, catalog: dict) -> list[PreflightIssue]:
    count = sum(
        1 for repo in (catalog.get("spec") or {}).get("repositories") or []
        if repo.get("type") == "helm"
    )
    if count > 1:
        return [PreflightWarning("catalog has multiple helm repositories; using the first")]
    return []


def check_dependency_helm_releases(app: dict, catalog: dict, dependency_helm_releases: dict) -> list[PreflightIssue]:
    ns = app.get("metadata", {}).get("namespace", "")
    return [
        PreflightError(
            f'HelmRelease "{name}" not found in namespace "{ns}"; '
            "ensure all dependencies are migrated before migrating this app"
        )
        for name, hr in dependency_helm_releases.items()
        if hr is None
    ]


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
    check_psp_removal_patch,
    check_multiple_helm_repos,
    check_oci_fallback,
    check_flux_managed,
]

_REFS_CHECKS = [
    check_cross_namespace_refs,
    check_missing_referenced_configs,
    check_empty_referenced_configs,
]


def run_preflight(
    app: dict,
    catalog: dict,
    referenced_configs: dict | None = None,
    dependency_helm_releases: dict | None = None,
) -> list[PreflightIssue]:
    refs = referenced_configs or {}
    deps = dependency_helm_releases or {}
    return (
        [issue for check in _CHECKS for issue in check(app, catalog)]
        + [issue for check in _REFS_CHECKS for issue in check(app, catalog, refs)]
        + check_dependency_helm_releases(app, catalog, deps)
    )
