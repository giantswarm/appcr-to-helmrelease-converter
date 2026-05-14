class PreflightWarning(Exception):
    pass


class PreflightError(Exception):
    pass


def check_kube_config(app: dict) -> None:
    kube_config = app.get("spec", {}).get("kubeConfig", {})
    if not kube_config or kube_config.get("inCluster"):
        return
    secret_ns = kube_config.get("secret", {}).get("namespace")
    app_ns = app["metadata"]["namespace"]
    if secret_ns and secret_ns != app_ns:
        raise PreflightError(
            f'spec.kubeConfig.secret.namespace "{secret_ns}" does not match app namespace "{app_ns}";'
            " Flux requires the kubeconfig Secret to be in the same namespace as the HelmRelease"
        )


def check_namespace_config(app: dict) -> None:
    if app.get("spec", {}).get("namespaceConfig"):
        raise PreflightWarning(
            "spec.namespaceConfig dropped (no HelmRelease equivalent; target namespace already exists)"
        )


_CHECKS = [check_kube_config, check_namespace_config]


def run_preflight(app: dict) -> list:
    issues = []
    for check in _CHECKS:
        try:
            check(app)
        except (PreflightWarning, PreflightError) as e:
            issues.append(e)
    return issues
