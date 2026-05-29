from preflight import (
    PreflightError,
    PreflightIssue,
    PreflightWarning,
    check_empty_values_from_names,
    check_kube_config,
    check_multiple_helm_repos,
    check_namespace_config,
    run_preflight,
)


APP_WITH_NAMESPACE_CONFIG = {
    "spec": {
        "namespaceConfig": {
            "annotations": {"linkerd.io/inject": "enabled"},
            "labels": {"some-label": "some-value"},
        }
    }
}

APP_WITHOUT_NAMESPACE_CONFIG = {
    "spec": {
        "name": "my-app",
        "namespace": "monitoring",
        "version": "1.2.3",
    }
}

_EMPTY_CATALOG = {}

_CATALOG_MULTI_HELM = {
    "spec": {"repositories": [
        {"type": "helm", "URL": "https://charts.example.io"},
        {"type": "helm", "URL": "https://mirror.example.io"},
    ]}
}

_CATALOG_SINGLE_HELM = {
    "spec": {"repositories": [{"type": "helm", "URL": "https://charts.example.io"}]}
}

_CATALOG_OCI = {
    "spec": {"repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/giantswarm"}]}
}


class TestPreflightIssueDisplay:
    def test_warning_display_contains_emoji(self):
        w = PreflightWarning("something dropped")
        assert "⚠️" in w.display()

    def test_warning_display_contains_message(self):
        w = PreflightWarning("something dropped")
        assert "something dropped" in w.display()

    def test_error_display_contains_emoji(self):
        e = PreflightError("namespace mismatch")
        assert "❌" in e.display()

    def test_error_display_contains_message(self):
        e = PreflightError("namespace mismatch")
        assert "namespace mismatch" in e.display()

    def test_warning_and_error_display_differ(self):
        w = PreflightWarning("msg")
        e = PreflightError("msg")
        assert w.display() != e.display()


class TestCheckNamespaceConfig:
    def test_returns_warning_when_namespace_config_present(self):
        result = check_namespace_config(APP_WITH_NAMESPACE_CONFIG, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_returns_empty_list_when_namespace_config_absent(self):
        assert check_namespace_config(APP_WITHOUT_NAMESPACE_CONFIG, _EMPTY_CATALOG) == []


class TestCheckKubeConfig:
    def test_passes_when_kube_config_absent(self):
        assert check_kube_config({"metadata": {"namespace": "org-giantswarm"}, "spec": {}}, _EMPTY_CATALOG) == []

    def test_passes_when_in_cluster(self):
        assert check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {"inCluster": True}},
        }, _EMPTY_CATALOG) == []

    def test_error_when_secret_namespace_differs_from_app_namespace(self):
        result = check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {
                "inCluster": False,
                "secret": {"name": "example-kubeconfig", "namespace": "other-ns"},
            }},
        }, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightError)

    def test_error_when_in_cluster_absent_and_secret_namespace_differs(self):
        result = check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {
                "secret": {"name": "example-kubeconfig", "namespace": "other-ns"},
            }},
        }, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightError)

    def test_passes_when_secret_namespace_matches_app_namespace(self):
        assert check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {
                "inCluster": False,
                "secret": {"name": "example-kubeconfig", "namespace": "org-giantswarm"},
            }},
        }, _EMPTY_CATALOG) == []


class TestCheckEmptyValuesFromNames:
    def test_config_secret_empty_name_returns_warning(self):
        app = {"spec": {"config": {"secret": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.config.secret" in str(result[0])

    def test_config_configmap_empty_name_returns_warning(self):
        app = {"spec": {"config": {"configMap": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.config.configMap" in str(result[0])

    def test_userconfig_secret_empty_name_returns_warning(self):
        app = {"spec": {"userConfig": {"secret": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.userConfig.secret" in str(result[0])

    def test_userconfig_configmap_empty_name_returns_warning(self):
        app = {"spec": {"userConfig": {"configMap": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.userConfig.configMap" in str(result[0])

    def test_two_empty_name_fields_return_two_warnings(self):
        app = {"spec": {
            "config": {"secret": {"name": "", "namespace": "org-x"}},
            "userConfig": {"configMap": {"name": "", "namespace": "org-x"}},
        }}
        result = check_empty_values_from_names(app, _EMPTY_CATALOG)
        assert len(result) == 2
        paths = [str(w) for w in result]
        assert any("spec.config.secret" in p for p in paths)
        assert any("spec.userConfig.configMap" in p for p in paths)

    def test_no_empty_names_returns_empty_list(self):
        app = {"spec": {
            "config": {"configMap": {"name": "cm", "namespace": "org-x"}},
            "userConfig": {"secret": {"name": "s", "namespace": "org-x"}},
        }}
        assert check_empty_values_from_names(app, _EMPTY_CATALOG) == []


class TestCheckMultipleHelmRepos:
    def test_returns_warning_when_multiple_helm_repos(self):
        result = check_multiple_helm_repos({}, _CATALOG_MULTI_HELM)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_returns_empty_list_for_single_helm_repo(self):
        assert check_multiple_helm_repos({}, _CATALOG_SINGLE_HELM) == []

    def test_returns_empty_list_for_oci_catalog(self):
        assert check_multiple_helm_repos({}, _CATALOG_OCI) == []

    def test_returns_empty_list_for_empty_catalog(self):
        assert check_multiple_helm_repos({}, _EMPTY_CATALOG) == []

    def test_returns_empty_list_when_repositories_absent(self):
        assert check_multiple_helm_repos({}, {"spec": {}}) == []


class TestRunPreflight:
    def test_returns_warning_when_namespace_config_present(self):
        issues = run_preflight(APP_WITH_NAMESPACE_CONFIG, _EMPTY_CATALOG)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_returns_empty_list_when_no_issues(self):
        assert run_preflight(APP_WITHOUT_NAMESPACE_CONFIG, _EMPTY_CATALOG) == []

    def test_collects_empty_values_from_name_warnings(self):
        app = {"spec": {"config": {"secret": {"name": "", "namespace": "org-x"}}}}
        issues = run_preflight(app, _EMPTY_CATALOG)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_collects_catalog_warning_alongside_app_warnings(self):
        app = {"spec": {"namespaceConfig": {"annotations": {}}}}
        issues = run_preflight(app, _CATALOG_MULTI_HELM)
        assert len(issues) == 2
        assert all(isinstance(i, PreflightWarning) for i in issues)

    def test_warnings_before_errors_regardless_of_check_order(self):
        app = {
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {
                "namespaceConfig": {"annotations": {}},
                "kubeConfig": {
                    "inCluster": False,
                    "secret": {"name": "kc", "namespace": "other-ns"},
                },
            },
        }
        issues = run_preflight(app, _EMPTY_CATALOG)
        assert any(isinstance(i, PreflightError) for i in issues)
        assert any(isinstance(i, PreflightWarning) for i in issues)
