from fetcher import ReleaseChartFacts
from preflight import (
    PreflightError,
    PreflightIssue,
    PreflightWarning,
    check_app_status,
    check_dependency_helm_releases,
    check_empty_values_from_names,
    check_flux_managed,
    check_kube_config,
    check_multiple_helm_repos,
    check_namespace_config,
    check_oci_fallback,
    check_psp_removal_patch,
    check_pull_secret,
    check_registry_override,
    check_release_chart,
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


def _deployed(app):
    version = (app.get("spec") or {}).get("version")
    return {**app, "status": {"release": {"status": "deployed"}, "version": version}}

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


class TestCheckPspRemovalPatch:
    def test_present_returns_warning(self):
        app = {
            "metadata": {"namespace": "ns"},
            "spec": {"extraConfigs": [{"name": "psp-removal-patch", "namespace": "ns"}]},
        }
        result = check_psp_removal_patch(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)
        assert "psp-removal-patch" in str(result[0])

    def test_absent_returns_empty_list(self):
        app = {
            "metadata": {"namespace": "ns"},
            "spec": {"extraConfigs": [{"name": "other-config", "namespace": "ns"}]},
        }
        assert check_psp_removal_patch(app, _EMPTY_CATALOG) == []

    def test_no_extra_configs_returns_empty_list(self):
        assert check_psp_removal_patch({"metadata": {"namespace": "ns"}, "spec": {}}, _EMPTY_CATALOG) == []

    def test_suffixed_name_returns_warning(self):
        app = {
            "metadata": {"namespace": "ns"},
            "spec": {"extraConfigs": [{"name": "psp-removal-patch-datadog", "namespace": "ns"}]},
        }
        result = check_psp_removal_patch(app, _EMPTY_CATALOG)
        assert len(result) == 1
        assert "psp-removal-patch-datadog" in str(result[0])

    def test_different_namespace_returns_empty_list(self):
        app = {
            "metadata": {"namespace": "ns"},
            "spec": {"extraConfigs": [{"name": "psp-removal-patch", "namespace": "other-ns"}]},
        }
        assert check_psp_removal_patch(app, _EMPTY_CATALOG) == []


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


_APP_NS = "org-acme"
_APP_BASE = {"metadata": {"namespace": _APP_NS}, "spec": {}}


class TestCheckMissingReferencedConfigs:
    def test_none_value_returns_error(self):
        refs = {("ConfigMap", "cm", _APP_NS): None}
        from preflight import check_missing_referenced_configs
        result = check_missing_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert len(result) == 1
        assert isinstance(result[0], PreflightError)

    def test_present_resource_returns_empty(self):
        refs = {("ConfigMap", "cm", _APP_NS): {"data": {"values.yaml": "x"}}}
        from preflight import check_missing_referenced_configs
        result = check_missing_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert result == []

    def test_error_message_contains_name(self):
        refs = {("ConfigMap", "my-config", _APP_NS): None}
        from preflight import check_missing_referenced_configs
        result = check_missing_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert "my-config" in str(result[0])


class TestCheckEmptyReferencedConfigs:
    def test_empty_data_returns_warning(self):
        refs = {("ConfigMap", "cm", _APP_NS): {"data": {}}}
        from preflight import check_empty_referenced_configs
        result = check_empty_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_none_data_returns_warning(self):
        refs = {("ConfigMap", "cm", _APP_NS): {"data": None}}
        from preflight import check_empty_referenced_configs
        result = check_empty_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_resource_with_keys_returns_empty(self):
        refs = {("ConfigMap", "cm", _APP_NS): {"data": {"values.yaml": "x"}}}
        from preflight import check_empty_referenced_configs
        result = check_empty_referenced_configs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert result == []


class TestCheckCrossNamespaceRefs:
    def test_different_namespace_returns_error(self):
        refs = {("ConfigMap", "cm", "other-ns"): {"data": {"values.yaml": "x"}}}
        from preflight import check_cross_namespace_refs
        result = check_cross_namespace_refs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert len(result) == 1
        assert isinstance(result[0], PreflightError)

    def test_same_namespace_returns_empty(self):
        refs = {("ConfigMap", "cm", _APP_NS): {"data": {"values.yaml": "x"}}}
        from preflight import check_cross_namespace_refs
        result = check_cross_namespace_refs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert result == []

    def test_error_message_contains_both_namespaces(self):
        refs = {("ConfigMap", "cm", "other-ns"): {"data": {}}}
        from preflight import check_cross_namespace_refs
        result = check_cross_namespace_refs(_APP_BASE, _EMPTY_CATALOG, refs)
        assert "other-ns" in str(result[0])
        assert _APP_NS in str(result[0])


_APP_FLUX_MANAGED = {
    "metadata": {
        "labels": {
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
            "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
        }
    },
    "spec": {},
}

_APP_NO_LABELS = {"metadata": {}, "spec": {}}

_CATALOG_STORAGE_OCI = {"spec": {"storage": {"type": "oci", "URL": "oci://gsoci.azurecr.io/giantswarm"}}}


class TestCheckOciFallback:
    def test_oci_catalog_returns_empty_list(self):
        assert check_oci_fallback(_APP_NO_LABELS, _CATALOG_OCI) == []

    def test_helm_catalog_returns_warning(self):
        result = check_oci_fallback(_APP_NO_LABELS, _CATALOG_SINGLE_HELM)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_empty_catalog_returns_warning(self):
        result = check_oci_fallback(_APP_NO_LABELS, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_storage_oci_returns_empty_list(self):
        assert check_oci_fallback(_APP_NO_LABELS, _CATALOG_STORAGE_OCI) == []


class TestCheckFluxManaged:
    def test_both_kustomize_labels_returns_warning(self):
        result = check_flux_managed(_APP_FLUX_MANAGED, _EMPTY_CATALOG)
        assert len(result) == 1
        assert isinstance(result[0], PreflightWarning)

    def test_warning_message_mentions_app_cr_finalizer(self):
        result = check_flux_managed(_APP_FLUX_MANAGED, _EMPTY_CATALOG)
        assert "operatorkit.giantswarm.io/app-operator-app" in str(result[0])

    def test_warning_message_mentions_chart_cr_finalizer(self):
        result = check_flux_managed(_APP_FLUX_MANAGED, _EMPTY_CATALOG)
        assert "operatorkit.giantswarm.io/chart-operator-chart" in str(result[0])

    def test_only_name_label_returns_empty(self):
        app = {"metadata": {"labels": {"kustomize.toolkit.fluxcd.io/name": "k"}}, "spec": {}}
        assert check_flux_managed(app, _EMPTY_CATALOG) == []

    def test_only_namespace_label_returns_empty(self):
        app = {"metadata": {"labels": {"kustomize.toolkit.fluxcd.io/namespace": "flux-system"}}, "spec": {}}
        assert check_flux_managed(app, _EMPTY_CATALOG) == []

    def test_no_labels_returns_empty(self):
        assert check_flux_managed(_APP_NO_LABELS, _EMPTY_CATALOG) == []


class TestRunPreflight:
    def test_returns_warning_when_namespace_config_present(self):
        issues = run_preflight(_deployed(APP_WITH_NAMESPACE_CONFIG), _CATALOG_OCI)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_returns_empty_list_when_no_issues(self):
        assert run_preflight(_deployed(APP_WITHOUT_NAMESPACE_CONFIG), _CATALOG_OCI) == []

    def test_collects_empty_values_from_name_warnings(self):
        app = {"spec": {"config": {"secret": {"name": "", "namespace": "org-x"}}}}
        issues = run_preflight(_deployed(app), _CATALOG_OCI)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)

    def test_collects_psp_removal_patch_warning(self):
        app = {
            "metadata": {"namespace": "ns"},
            "spec": {"extraConfigs": [{"name": "psp-removal-patch", "namespace": "ns"}]},
        }
        issues = run_preflight(_deployed(app), _CATALOG_OCI)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)
        assert "psp-removal-patch" in str(issues[0])

    def test_collects_catalog_warning_alongside_app_warnings(self):
        app = {"spec": {"namespaceConfig": {"annotations": {}}}}
        issues = run_preflight(_deployed(app), _CATALOG_MULTI_HELM)
        assert len(issues) >= 2
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
        issues = run_preflight(_deployed(app), _CATALOG_OCI)
        assert any(isinstance(i, PreflightError) for i in issues)
        assert any(isinstance(i, PreflightWarning) for i in issues)

    def test_oci_fallback_warning_surfaced_by_run_preflight(self):
        issues = run_preflight(_deployed(APP_WITHOUT_NAMESPACE_CONFIG), _CATALOG_SINGLE_HELM)
        assert any("HelmRepository" in str(i) for i in issues)

    def test_flux_managed_warning_surfaced_by_run_preflight(self):
        issues = run_preflight(_deployed(_APP_FLUX_MANAGED), _CATALOG_OCI)
        assert any("gitops" in str(i) for i in issues)


_DEP_APP = {"metadata": {"namespace": "giantswarm"}, "spec": {}}
_HR_FOUND = {"metadata": {"name": "coredns"}}


class TestCheckDependencyHelmReleases:
    def test_no_deps_no_errors(self):
        assert check_dependency_helm_releases(_DEP_APP, {}, {}) == []

    def test_dep_found_no_errors(self):
        assert check_dependency_helm_releases(_DEP_APP, {}, {"coredns": _HR_FOUND}) == []

    def test_dep_missing_is_preflight_error(self):
        issues = check_dependency_helm_releases(_DEP_APP, {}, {"coredns": None})
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightError)

    def test_error_names_missing_helmrelease(self):
        issues = check_dependency_helm_releases(_DEP_APP, {}, {"coredns": None})
        assert "coredns" in str(issues[0])

    def test_error_names_namespace(self):
        issues = check_dependency_helm_releases(_DEP_APP, {}, {"coredns": None})
        assert "giantswarm" in str(issues[0])

    def test_multiple_missing_one_error_each(self):
        issues = check_dependency_helm_releases(_DEP_APP, {}, {"coredns": None, "prometheus": None})
        assert len(issues) == 2

    def test_mixed_found_and_missing(self):
        issues = check_dependency_helm_releases(_DEP_APP, {}, {"coredns": _HR_FOUND, "prometheus": None})
        assert len(issues) == 1
        assert "prometheus" in str(issues[0])

    def test_surfaced_by_run_preflight(self):
        app = {"metadata": {"name": "my-app", "namespace": "giantswarm"}, "spec": {}}
        issues = run_preflight(_deployed(app), _EMPTY_CATALOG, dependency_helm_releases={"coredns": None})
        assert any(isinstance(i, PreflightError) and "coredns" in str(i) for i in issues)


_DOCKER_SECRET = {"type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": "e30="}}
_BASIC_AUTH_SECRET = {"type": "Opaque", "data": {"username": "dXNlcg==", "password": "cGFzcw=="}}
_APP_IN_NS = {"metadata": {"name": "my-app", "namespace": "giantswarm"}, "spec": {}}


class TestCheckPullSecret:
    def test_no_flag_returns_no_issues(self):
        assert check_pull_secret(_APP_IN_NS, _CATALOG_OCI, None, None) == []

    def test_no_flag_ignores_a_fetched_secret(self):
        assert check_pull_secret(_APP_IN_NS, _CATALOG_OCI, None, _DOCKER_SECRET) == []

    def test_missing_secret_is_an_error(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", None)
        assert isinstance(issues[0], PreflightError)

    def test_missing_secret_message_names_secret_and_namespace(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", None)
        assert "regcred" in str(issues[0]) and "giantswarm" in str(issues[0])

    def test_dockerconfigjson_secret_passes_oci_path(self):
        assert check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", _DOCKER_SECRET) == []

    def test_dockercfg_secret_passes_oci_path(self):
        secret = {"type": "kubernetes.io/dockercfg", "data": {".dockercfg": "e30="}}
        assert check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", secret) == []

    def test_basic_auth_secret_warns_on_oci_path(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", _BASIC_AUTH_SECRET)
        assert isinstance(issues[0], PreflightWarning)

    def test_oci_shape_warning_names_expected_type(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_OCI, "regcred", _BASIC_AUTH_SECRET)
        assert "kubernetes.io/dockerconfigjson" in str(issues[0])

    def test_basic_auth_secret_passes_helm_path(self):
        assert check_pull_secret(_APP_IN_NS, _CATALOG_SINGLE_HELM, "regcred", _BASIC_AUTH_SECRET) == []

    def test_dockerconfigjson_secret_warns_on_helm_path(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_SINGLE_HELM, "regcred", _DOCKER_SECRET)
        assert isinstance(issues[0], PreflightWarning)

    def test_helm_shape_warning_names_missing_keys(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_SINGLE_HELM, "regcred", _DOCKER_SECRET)
        assert "username" in str(issues[0]) and "password" in str(issues[0])

    def test_helm_path_warns_when_only_password_missing(self):
        secret = {"type": "Opaque", "data": {"username": "dXNlcg=="}}
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_SINGLE_HELM, "regcred", secret)
        assert "password" in str(issues[0]) and "username" not in str(issues[0]).split(";")[0]

    def test_helm_path_warns_on_secret_with_no_data(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_SINGLE_HELM, "regcred", {"type": "Opaque"})
        assert isinstance(issues[0], PreflightWarning)

    def test_missing_secret_surfaced_by_run_preflight(self):
        issues = run_preflight(_deployed(_APP_IN_NS), _CATALOG_OCI, pull_secret_name="regcred", pull_secret=None)
        assert any(isinstance(i, PreflightError) and "regcred" in str(i) for i in issues)

    def test_shape_warning_surfaced_by_run_preflight(self):
        issues = run_preflight(
            _deployed(_APP_IN_NS), _CATALOG_OCI, pull_secret_name="regcred", pull_secret=_BASIC_AUTH_SECRET
        )
        assert any(isinstance(i, PreflightWarning) and "regcred" in str(i) for i in issues)

    def test_dockerconfigjson_secret_passes_storage_style_oci_catalog(self):
        assert check_pull_secret(_APP_IN_NS, _CATALOG_STORAGE_OCI, "regcred", _DOCKER_SECRET) == []

    def test_basic_auth_secret_warns_on_storage_style_oci_catalog(self):
        issues = check_pull_secret(_APP_IN_NS, _CATALOG_STORAGE_OCI, "regcred", _BASIC_AUTH_SECRET)
        assert isinstance(issues[0], PreflightWarning)
        assert "kubernetes.io/dockerconfigjson" in str(issues[0])


class TestCheckRegistryOverride:
    def test_no_flag_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_OCI, None) == []

    def test_empty_string_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_OCI, "") == []

    def test_bare_host_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_OCI, "gsociprivate.azurecr.io") == []

    def test_non_oci_scheme_against_oci_catalog_is_an_error(self):
        issues = check_registry_override(_APP_IN_NS, _CATALOG_OCI, "https://gsociprivate.azurecr.io")
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightError)

    def test_non_oci_scheme_error_names_value_and_reason(self):
        issues = check_registry_override(_APP_IN_NS, _CATALOG_OCI, "https://gsociprivate.azurecr.io")
        message = str(issues[0])
        assert "https://gsociprivate.azurecr.io" in message
        assert "oci://" in message

    def test_matching_oci_scheme_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_OCI, "oci://gsociprivate.azurecr.io") == []

    def test_uppercase_oci_scheme_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_OCI, "OCI://gsociprivate.azurecr.io") == []

    def test_mismatched_scheme_against_helm_catalog_is_an_error(self):
        issues = check_registry_override(_APP_IN_NS, _CATALOG_SINGLE_HELM, "oci://gsociprivate.azurecr.io")
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightError)

    def test_mismatched_scheme_error_names_both_schemes(self):
        issues = check_registry_override(_APP_IN_NS, _CATALOG_SINGLE_HELM, "oci://gsociprivate.azurecr.io")
        message = str(issues[0])
        assert "oci" in message and "https" in message

    def test_matching_helm_scheme_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_SINGLE_HELM, "https://mirror.example.io") == []

    def test_matching_helm_scheme_case_variant_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _CATALOG_SINGLE_HELM, "HTTPS://mirror.example.io") == []

    def test_helm_only_catalog_with_no_helm_entry_returns_no_issues(self):
        assert check_registry_override(_APP_IN_NS, _EMPTY_CATALOG, "https://mirror.example.io") == []

    def test_default_is_no_op_via_run_preflight(self):
        assert run_preflight(_deployed(APP_WITHOUT_NAMESPACE_CONFIG), _CATALOG_OCI) == []

    def test_mismatch_surfaced_by_run_preflight(self):
        issues = run_preflight(
            _deployed(APP_WITHOUT_NAMESPACE_CONFIG), _CATALOG_OCI, registry_override="https://gsociprivate.azurecr.io"
        )
        assert any(isinstance(i, PreflightError) for i in issues)


class TestCheckAppStatus:
    def _app(self, release_status="deployed", deployed="1.2.3", wanted="1.2.3"):
        status = {}
        if release_status is not None:
            status["release"] = {"status": release_status}
        if deployed is not None:
            status["version"] = deployed
        return {"spec": {"version": wanted}, "status": status}

    def test_deployed_app_passes(self):
        assert check_app_status(self._app(), _EMPTY_CATALOG) == []

    def test_failed_release_is_error(self):
        issues = check_app_status(self._app(release_status="failed"), _EMPTY_CATALOG)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightError)
        assert 'release status "failed"' in str(issues[0])
        assert "--ignore-app-status" in str(issues[0])

    def test_version_mismatch_is_error(self):
        issues = check_app_status(self._app(deployed="1.2.2"), _EMPTY_CATALOG)
        assert isinstance(issues[0], PreflightError)
        assert 'status.version "1.2.2"' in str(issues[0])
        assert 'spec.version "1.2.3"' in str(issues[0])

    def test_missing_status_is_error(self):
        issues = check_app_status({"spec": {}}, _EMPTY_CATALOG)
        assert isinstance(issues[0], PreflightError)
        assert 'release status "<unset>"' in str(issues[0])
        assert 'status.version "<unset>"' in str(issues[0])
        assert 'spec.version "<unset>"' in str(issues[0])

    def test_ignore_turns_error_into_warning(self):
        issues = check_app_status(self._app(release_status="failed"), _EMPTY_CATALOG, ignore_app_status=True)
        assert len(issues) == 1
        assert isinstance(issues[0], PreflightWarning)
        assert 'release status "failed"' in str(issues[0])

    def test_ignore_on_deployed_app_is_silent(self):
        assert check_app_status(self._app(), _EMPTY_CATALOG, ignore_app_status=True) == []

    def test_surfaced_by_run_preflight(self):
        issues = run_preflight(self._app(release_status="pending-upgrade"), _CATALOG_OCI)
        assert any(isinstance(i, PreflightError) and "pending-upgrade" in str(i) for i in issues)

    def test_ignore_passed_through_run_preflight(self):
        issues = run_preflight(self._app(release_status="failed"), _CATALOG_OCI, ignore_app_status=True)
        assert not any(isinstance(i, PreflightError) for i in issues)


_CLUSTER_APP = {
    "metadata": {"name": "mycluster", "namespace": "org-acme"},
    "spec": {"name": "cluster-aws", "namespace": "org-acme", "version": "7.2.5",
             "kubeConfig": {"inCluster": True}},
}
_CAPI_CLUSTER = {"metadata": {"name": "mycluster", "labels": {"release.giantswarm.io/version": "34.0.0"}}}
_RELEASE_CR = {"spec": {"components": [{"name": "cluster-aws", "version": "7.2.5"}]}}
_CLUSTER_CATALOG = {"metadata": {"name": "cluster"},
                    "spec": {"repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/charts/giantswarm/"}]}}


def _facts(**overrides):
    values = dict(provider="aws", clusters=[_CAPI_CLUSTER], release_version="34.0.0",
                  release_cr=_RELEASE_CR, published=True)
    values.update(overrides)
    return ReleaseChartFacts(**values)


class TestCheckReleaseChart:
    def _errors(self, facts, catalog=_CLUSTER_CATALOG):
        issues = check_release_chart(_CLUSTER_APP, catalog, facts)
        assert all(isinstance(i, PreflightError) for i in issues)
        return [str(i) for i in issues]

    def test_no_facts_is_no_op(self):
        assert check_release_chart(_CLUSTER_APP, _CLUSTER_CATALOG, None) == []

    def test_complete_facts_pass(self):
        assert self._errors(_facts()) == []

    def test_helm_only_catalog_is_error(self):
        catalog = {"metadata": {"name": "cluster"},
                   "spec": {"repositories": [{"type": "helm", "URL": "https://x"}]}}
        errors = self._errors(_facts(), catalog)
        assert len(errors) == 1
        assert 'Catalog "cluster" has none' in errors[0]
        assert "release-aws" in errors[0]

    def test_no_cluster_is_error(self):
        errors = self._errors(_facts(clusters=[], release_version=None, release_cr=None, published=None))
        assert errors == [
            'no Cluster labelled app.kubernetes.io/instance=mycluster found in namespace "org-acme"; '
            "cannot determine the release version for the Release chart"
        ]

    def test_several_clusters_is_error(self):
        other = {"metadata": {"name": "other"}}
        errors = self._errors(_facts(clusters=[_CAPI_CLUSTER, other], release_version=None))
        assert len(errors) == 1
        assert "2 Clusters" in errors[0]
        assert "(mycluster, other)" in errors[0]

    def test_missing_version_label_is_error(self):
        errors = self._errors(_facts(release_version=None, release_cr=None, published=None))
        assert errors == [
            'Cluster "mycluster" has no release.giantswarm.io/version label; '
            "cannot determine the release version for the Release chart"
        ]

    def test_missing_release_cr_is_error(self):
        errors = self._errors(_facts(release_cr=None, published=None))
        assert errors == ['Release CR "aws-34.0.0" not found (named by Cluster "mycluster")']

    def test_version_mismatch_is_error(self):
        release = {"spec": {"components": [{"name": "cluster-aws", "version": "7.2.6"}]}}
        errors = self._errors(_facts(release_cr=release))
        assert len(errors) == 1
        assert "pins cluster-aws 7.2.6" in errors[0]
        assert "spec.version is 7.2.5" in errors[0]

    def test_release_cr_without_cluster_chart_is_error(self):
        errors = self._errors(_facts(release_cr={"spec": {"components": []}}))
        assert "pins cluster-aws <none>" in errors[0]

    def test_unpublished_release_chart_is_error(self):
        errors = self._errors(_facts(published=False))
        assert errors == [
            "release-aws:34.0.0 not found in gsoci.azurecr.io/charts/giantswarm; "
            'there is no Release chart for Release CR "aws-34.0.0"'
        ]

    def test_catalog_and_resolution_errors_both_reported(self):
        catalog = {"metadata": {"name": "cluster"}, "spec": {}}
        assert len(self._errors(_facts(published=False), catalog)) == 2

    def test_surfaced_by_run_preflight(self):
        issues = run_preflight(_deployed(_CLUSTER_APP), _CLUSTER_CATALOG, release_chart=_facts(published=False))
        assert any(isinstance(i, PreflightError) and "release-aws:34.0.0" in str(i) for i in issues)
