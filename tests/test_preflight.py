import pytest

from preflight import PreflightError, PreflightWarning, check_kube_config, check_namespace_config, run_preflight


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


class TestRunPreflight:
    def test_returns_warning_when_namespace_config_present(self):
        issues = run_preflight(APP_WITH_NAMESPACE_CONFIG)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_returns_empty_list_when_no_issues(self):
        assert run_preflight(APP_WITHOUT_NAMESPACE_CONFIG) == []
