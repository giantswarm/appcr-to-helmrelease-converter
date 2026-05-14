import pytest

from preflight import PreflightError, PreflightWarning, check_empty_values_from_names, check_kube_config, check_namespace_config, run_preflight


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


class TestCheckNamespaceConfig:
    def test_raises_warning_when_namespace_config_present(self):
        with pytest.raises(PreflightWarning):
            check_namespace_config(APP_WITH_NAMESPACE_CONFIG)

    def test_passes_silently_when_namespace_config_absent(self):
        check_namespace_config(APP_WITHOUT_NAMESPACE_CONFIG)


class TestCheckKubeConfig:
    def test_passes_when_kube_config_absent(self):
        check_kube_config({"metadata": {"namespace": "org-giantswarm"}, "spec": {}})

    def test_passes_when_in_cluster(self):
        check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {"inCluster": True}},
        })

    def test_error_when_secret_namespace_differs_from_app_namespace(self):
        with pytest.raises(PreflightError):
            check_kube_config({
                "metadata": {"namespace": "org-giantswarm"},
                "spec": {"kubeConfig": {
                    "inCluster": False,
                    "secret": {"name": "example-kubeconfig", "namespace": "other-ns"},
                }},
            })

    def test_error_when_in_cluster_absent_and_secret_namespace_differs(self):
        with pytest.raises(PreflightError):
            check_kube_config({
                "metadata": {"namespace": "org-giantswarm"},
                "spec": {"kubeConfig": {
                    "secret": {"name": "example-kubeconfig", "namespace": "other-ns"},
                }},
            })

    def test_passes_when_secret_namespace_matches_app_namespace(self):
        check_kube_config({
            "metadata": {"namespace": "org-giantswarm"},
            "spec": {"kubeConfig": {
                "inCluster": False,
                "secret": {"name": "example-kubeconfig", "namespace": "org-giantswarm"},
            }},
        })


class TestCheckEmptyValuesFromNames:
    def test_config_secret_empty_name_returns_warning(self):
        app = {"spec": {"config": {"secret": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.config.secret" in str(result[0])

    def test_config_configmap_empty_name_returns_warning(self):
        app = {"spec": {"config": {"configMap": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.config.configMap" in str(result[0])

    def test_userconfig_secret_empty_name_returns_warning(self):
        app = {"spec": {"userConfig": {"secret": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.userConfig.secret" in str(result[0])

    def test_userconfig_configmap_empty_name_returns_warning(self):
        app = {"spec": {"userConfig": {"configMap": {"name": "", "namespace": "org-x"}}}}
        result = check_empty_values_from_names(app)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "spec.userConfig.configMap" in str(result[0])

    def test_two_empty_name_fields_return_two_warnings(self):
        app = {"spec": {
            "config": {"secret": {"name": "", "namespace": "org-x"}},
            "userConfig": {"configMap": {"name": "", "namespace": "org-x"}},
        }}
        result = check_empty_values_from_names(app)
        assert len(result) == 2
        paths = [str(w) for w in result]
        assert any("spec.config.secret" in p for p in paths)
        assert any("spec.userConfig.configMap" in p for p in paths)

    def test_no_empty_names_returns_empty_list(self):
        app = {"spec": {
            "config": {"configMap": {"name": "cm", "namespace": "org-x"}},
            "userConfig": {"secret": {"name": "s", "namespace": "org-x"}},
        }}
        assert check_empty_values_from_names(app) == []


class TestRunPreflight:
    def test_returns_warning_when_namespace_config_present(self):
        issues = run_preflight(APP_WITH_NAMESPACE_CONFIG)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_returns_empty_list_when_no_issues(self):
        assert run_preflight(APP_WITHOUT_NAMESPACE_CONFIG) == []

    def test_collects_empty_values_from_name_warnings(self):
        app = {"spec": {"config": {"secret": {"name": "", "namespace": "org-x"}}}}
        issues = run_preflight(app)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)
