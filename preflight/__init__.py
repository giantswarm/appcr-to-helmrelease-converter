class PreflightWarning(Exception):
    pass


class PreflightError(Exception):
    pass


def check_namespace_config(app: dict) -> None:
    if app.get("spec", {}).get("namespaceConfig"):
        raise PreflightWarning(
            "spec.namespaceConfig dropped (no HelmRelease equivalent; target namespace already exists)"
        )


_CHECKS = [check_namespace_config]


def run_preflight(app: dict) -> list:
    issues = []
    for check in _CHECKS:
        try:
            check(app)
        except (PreflightWarning, PreflightError) as e:
            issues.append(e)
    return issues
