from collections import OrderedDict

from converter.resources import build_oci_repository, build_helm_release, build_helm_release_and_helm_repo


def _app(*, name="my-app", namespace="giantswarm", spec_name="my-app", spec_namespace="monitoring",
         annotations=None, labels=None, spec_extra=None):
    spec = {"name": spec_name, "namespace": spec_namespace, "version": "1.0.0"}
    if spec_extra:
        spec.update(spec_extra)
    meta = {"name": name, "namespace": namespace}
    if annotations:
        meta["annotations"] = annotations
    if labels:
        meta["labels"] = labels
    return {"metadata": meta, "spec": spec}


def _catalog(url="oci://gsoci.azurecr.io/charts/giantswarm"):
    return {"spec": {"repositories": [{"type": "oci", "URL": url}]}}


# ---------------------------------------------------------------------------
# build_helm_release
# ---------------------------------------------------------------------------

class TestBuildHelmRelease:
    def test_kind(self):
        assert build_helm_release(_app())["kind"] == "HelmRelease"

    def test_api_version(self):
        assert build_helm_release(_app())["apiVersion"] == "helm.toolkit.fluxcd.io/v2"

    def test_metadata_name(self):
        assert build_helm_release(_app(name="my-app"))["metadata"]["name"] == "my-app"

    def test_metadata_namespace(self):
        assert build_helm_release(_app(namespace="giantswarm"))["metadata"]["namespace"] == "giantswarm"

    def test_release_name_from_metadata_name(self):
        assert build_helm_release(_app(name="my-app"))["spec"]["releaseName"] == "my-app"

    def test_release_name_strips_cluster_prefix(self):
        app = _app(name="mycluster-my-app", labels={"giantswarm.io/cluster": "mycluster"})
        assert build_helm_release(app)["spec"]["releaseName"] == "my-app"

    def test_release_name_strips_cluster_suffix(self):
        app = _app(name="my-app-mycluster", labels={"giantswarm.io/cluster": "mycluster"})
        assert build_helm_release(app)["spec"]["releaseName"] == "my-app"

    def test_release_name_no_cluster_label_unchanged(self):
        assert build_helm_release(_app(name="mycluster-my-app"))["spec"]["releaseName"] == "mycluster-my-app"

    def test_target_namespace_from_spec_namespace(self):
        assert build_helm_release(_app(spec_namespace="monitoring"))["spec"]["targetNamespace"] == "monitoring"

    def test_storage_namespace_from_spec_namespace(self):
        assert build_helm_release(_app(spec_namespace="monitoring"))["spec"]["storageNamespace"] == "monitoring"

    def test_chart_ref_kind(self):
        assert build_helm_release(_app())["spec"]["chartRef"]["kind"] == "OCIRepository"

    def test_chart_ref_name_from_metadata_name(self):
        assert build_helm_release(_app(name="my-app"))["spec"]["chartRef"]["name"] == "my-app"

    def test_chart_ref_namespace_from_metadata_namespace(self):
        assert build_helm_release(_app(namespace="giantswarm"))["spec"]["chartRef"]["namespace"] == "giantswarm"

    def test_interval(self):
        assert build_helm_release(_app())["spec"]["interval"] == "5m"

    def test_timeout(self):
        assert build_helm_release(_app())["spec"]["timeout"] == "10m"

    def test_install_remediation_retries(self):
        assert build_helm_release(_app())["spec"]["install"]["remediation"]["retries"] == 10

    def test_install_remediation_not_remediate_last_failure(self):
        assert build_helm_release(_app())["spec"]["install"]["remediation"]["remediateLastFailure"] is False

    def test_upgrade_remediation_remediate_last_failure(self):
        assert build_helm_release(_app())["spec"]["upgrade"]["remediation"]["remediateLastFailure"] is True

    def test_upgrade_remediation_retries(self):
        assert build_helm_release(_app())["spec"]["upgrade"]["remediation"]["retries"] == 10

    def test_upgrade_remediation_strategy_rollback(self):
        assert build_helm_release(_app())["spec"]["upgrade"]["remediation"]["strategy"] == "rollback"

    def test_depends_on_single(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns"}))
        assert result["spec"]["dependsOn"] == [{"name": "coredns"}]

    def test_depends_on_multiple(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns,prometheus"}))
        assert result["spec"]["dependsOn"] == [{"name": "coredns"}, {"name": "prometheus"}]

    def test_depends_on_whitespace_tolerated(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns, prometheus"}))
        assert result["spec"]["dependsOn"] == [{"name": "coredns"}, {"name": "prometheus"}]

    def test_depends_on_empty_entries_filtered(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns,,prometheus"}))
        assert result["spec"]["dependsOn"] == [{"name": "coredns"}, {"name": "prometheus"}]

    def test_depends_on_absent_means_no_depends_on_in_spec(self):
        assert "dependsOn" not in build_helm_release(_app())["spec"]

    def test_null_annotations_means_no_depends_on_in_spec(self):
        assert "dependsOn" not in build_helm_release(_app(annotations=None))["spec"]

    def test_explicit_null_annotations_and_labels_key_does_not_crash(self):
        app = _app()
        app["metadata"]["annotations"] = None
        app["metadata"]["labels"] = None
        assert "annotations" not in build_helm_release(app)["metadata"]
        assert "labels" not in build_helm_release(app)["metadata"]

    def test_depends_on_annotation_stripped_from_metadata(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns"}))
        assert "app-operator.giantswarm.io/depends-on" not in result["metadata"].get("annotations", {})

    def test_depends_on_helmrelease_annotation_stripped_from_metadata(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on-helmrelease": "true"}))
        assert "annotations" not in result["metadata"]

    def test_depends_on_appears_before_install_in_spec(self):
        result = build_helm_release(_app(annotations={"app-operator.giantswarm.io/depends-on": "coredns"}))
        assert list(result["spec"].keys())[0] == "dependsOn"

    def test_no_annotations_when_app_has_none(self):
        assert "annotations" not in build_helm_release(_app())["metadata"]

    def test_annotations_kept_when_not_in_blocklist(self):
        result = build_helm_release(_app(annotations={"custom.io/key": "value"}))
        assert result["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_blocked_annotations_removed(self):
        result = build_helm_release(_app(annotations={
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        }))
        assert "annotations" not in result["metadata"]

    def test_mixed_annotations_only_blocklisted_removed(self):
        result = build_helm_release(_app(annotations={
            "keep.me/key": "val",
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
        }))
        assert result["metadata"]["annotations"] == {"keep.me/key": "val"}

    def test_latest_configmap_version_annotation_removed(self):
        result = build_helm_release(_app(annotations={
            "app-operator.giantswarm.io/latest-configmap-version": "abc123",
        }))
        assert "annotations" not in result["metadata"]

    def test_latest_secret_version_annotation_removed(self):
        result = build_helm_release(_app(annotations={
            "app-operator.giantswarm.io/latest-secret-version": "abc123",
        }))
        assert "annotations" not in result["metadata"]

    def test_flux_annotation_removed(self):
        result = build_helm_release(_app(annotations={
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }))
        assert "annotations" not in result["metadata"]

    def test_non_flux_annotation_kept_when_mixed_with_flux(self):
        result = build_helm_release(_app(annotations={
            "custom.io/key": "value",
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }))
        assert result["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_no_labels_when_app_has_none(self):
        assert "labels" not in build_helm_release(_app())["metadata"]

    def test_labels_kept_when_not_in_blocklist(self):
        result = build_helm_release(_app(labels={"env": "prod"}))
        assert result["metadata"]["labels"] == {"env": "prod"}

    def test_blocked_label_removed(self):
        result = build_helm_release(_app(labels={"app-operator.giantswarm.io/version": "1.0.0"}))
        assert "labels" not in result["metadata"]

    def test_psp_status_label_removed(self):
        result = build_helm_release(_app(labels={"policy.giantswarm.io/psp-status": "removed"}))
        assert "labels" not in result["metadata"]

    def test_flux_label_removed(self):
        result = build_helm_release(_app(labels={
            "kustomize.toolkit.fluxcd.io/namespace": "my-namespace",
        }))
        assert "labels" not in result["metadata"]

    def test_no_kube_config_when_spec_kube_config_absent(self):
        assert "kubeConfig" not in build_helm_release(_app())["spec"]

    def test_no_kube_config_when_in_cluster(self):
        assert "kubeConfig" not in build_helm_release(
            _app(spec_extra={"kubeConfig": {"inCluster": True}})
        )["spec"]

    def test_kube_config_secret_ref_has_no_key_field(self):
        result = build_helm_release(_app(spec_extra={
            "kubeConfig": {
                "inCluster": False,
                "secret": {"name": "example-kubeconfig", "namespace": "org-giantswarm"},
            }
        }))
        assert "key" not in result["spec"]["kubeConfig"]["secretRef"]

    def test_kube_config_secret_ref_name_when_in_cluster_absent(self):
        result = build_helm_release(_app(spec_extra={
            "kubeConfig": {
                "secret": {"name": "example-kubeconfig", "namespace": "org-giantswarm"},
            }
        }))
        assert result["spec"]["kubeConfig"]["secretRef"]["name"] == "example-kubeconfig"

    def test_kube_config_secret_ref_name_when_remote_cluster(self):
        result = build_helm_release(_app(spec_extra={
            "kubeConfig": {
                "inCluster": False,
                "secret": {"name": "example-kubeconfig", "namespace": "org-giantswarm"},
            }
        }))
        assert result["spec"]["kubeConfig"]["secretRef"]["name"] == "example-kubeconfig"

    def test_service_account_name_set_for_org_namespace(self):
        assert build_helm_release(_app(spec_namespace="org-acme"))["spec"]["serviceAccountName"] == "automation"

    def test_no_service_account_name_for_giantswarm_namespace(self):
        assert "serviceAccountName" not in build_helm_release(_app(spec_namespace="giantswarm"))["spec"]

    def test_no_service_account_name_for_flux_giantswarm_namespace(self):
        assert "serviceAccountName" not in build_helm_release(_app(spec_namespace="flux-giantswarm"))["spec"]

    def test_no_service_account_name_for_monitoring_namespace(self):
        assert "serviceAccountName" not in build_helm_release(_app(spec_namespace="monitoring"))["spec"]

    def test_no_service_account_name_for_remote_cluster_in_org_namespace(self):
        app = _app(spec_namespace="org-acme", spec_extra={
            "kubeConfig": {"inCluster": False, "secret": {"name": "my-wc-kubeconfig"}},
        })
        assert "serviceAccountName" not in build_helm_release(app)["spec"]

    def test_service_account_name_set_when_in_cluster_true_in_org_namespace(self):
        app = _app(spec_namespace="org-acme", spec_extra={"kubeConfig": {"inCluster": True}})
        assert build_helm_release(app)["spec"]["serviceAccountName"] == "automation"

    def test_no_values_from_when_no_config(self):
        assert "valuesFrom" not in build_helm_release(_app())["spec"]

    def test_values_from_set_when_config_present(self):
        result = build_helm_release(_app(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        }))
        assert "valuesFrom" in result["spec"]
        assert result["spec"]["valuesFrom"][0]["name"] == "cluster-cm"


# ---------------------------------------------------------------------------
# build_oci_repository
# ---------------------------------------------------------------------------

class TestBuildOciRepository:
    def test_kind(self):
        assert build_oci_repository(_app(), _catalog())["kind"] == "OCIRepository"

    def test_api_version(self):
        assert build_oci_repository(_app(), _catalog())["apiVersion"] == "source.toolkit.fluxcd.io/v1"

    def test_metadata_name(self):
        assert build_oci_repository(_app(name="my-app"), _catalog())["metadata"]["name"] == "my-app"

    def test_metadata_namespace(self):
        assert build_oci_repository(_app(namespace="giantswarm"), _catalog())["metadata"]["namespace"] == "giantswarm"

    def test_url_uses_catalog_base_and_spec_name(self):
        cat = _catalog(url="oci://example.io/charts")
        assert build_oci_repository(_app(spec_name="kong-app"), cat)["spec"]["url"] == \
            "oci://example.io/charts/kong-app"

    def test_url_strips_trailing_slash_from_catalog(self):
        cat = _catalog(url="oci://example.io/charts/")
        assert build_oci_repository(_app(spec_name="my-app"), cat)["spec"]["url"] == \
            "oci://example.io/charts/my-app"

    def test_interval(self):
        assert build_oci_repository(_app(), _catalog())["spec"]["interval"] == "10m"

    def test_provider(self):
        assert build_oci_repository(_app(), _catalog())["spec"]["provider"] == "generic"

    def test_ref_tag_from_spec_version(self):
        assert build_oci_repository(_app(), _catalog())["spec"]["ref"]["tag"] == "1.0.0"

    def test_ref_tag_reflects_spec_version_value(self):
        assert build_oci_repository(_app(spec_extra={"version": "2.3.4"}), _catalog())["spec"]["ref"]["tag"] == "2.3.4"

    def test_empty_version_raises(self):
        import pytest
        with pytest.raises(ValueError, match="spec.version"):
            build_oci_repository(_app(spec_extra={"version": ""}), _catalog())

    def test_missing_version_raises(self):
        import pytest
        app = _app()
        del app["spec"]["version"]
        with pytest.raises(ValueError, match="spec.version"):
            build_oci_repository(app, _catalog())

    def test_storage_fallback_used_when_no_repositories(self):
        cat = {"spec": {"storage": {"type": "oci", "URL": "oci://fallback.io/charts"}}}
        result = build_oci_repository(_app(spec_name="my-app"), cat)
        assert result["spec"]["url"] == "oci://fallback.io/charts/my-app"

    def test_catalog_with_no_oci_type_raises(self):
        import pytest
        cat = {"spec": {"repositories": [{"type": "helm", "URL": "https://charts.example.io"}]}}
        with pytest.raises(ValueError, match="no oci repository"):
            build_oci_repository(_app(), cat)

    def test_annotations_kept_when_not_in_blocklist(self):
        result = build_oci_repository(_app(annotations={"custom.io/key": "value"}), _catalog())
        assert result["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_no_annotations_when_app_has_none(self):
        assert "annotations" not in build_oci_repository(_app(), _catalog())["metadata"]

    def test_blocked_annotations_removed(self):
        result = build_oci_repository(_app(annotations={
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        }), _catalog())
        assert "annotations" not in result["metadata"]

    def test_mixed_annotations_only_blocklisted_removed(self):
        result = build_oci_repository(_app(annotations={
            "keep.me/key": "val",
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
        }), _catalog())
        assert result["metadata"]["annotations"] == {"keep.me/key": "val"}

    def test_flux_annotation_removed(self):
        result = build_oci_repository(_app(annotations={
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }), _catalog())
        assert "annotations" not in result["metadata"]

    def test_non_flux_annotation_kept_when_mixed_with_flux(self):
        result = build_oci_repository(_app(annotations={
            "custom.io/key": "value",
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }), _catalog())
        assert result["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_latest_configmap_version_annotation_removed(self):
        result = build_oci_repository(_app(annotations={
            "app-operator.giantswarm.io/latest-configmap-version": "abc123",
        }), _catalog())
        assert "annotations" not in result["metadata"]

    def test_latest_secret_version_annotation_removed(self):
        result = build_oci_repository(_app(annotations={
            "app-operator.giantswarm.io/latest-secret-version": "abc123",
        }), _catalog())
        assert "annotations" not in result["metadata"]

    def test_depends_on_annotation_stripped_from_metadata(self):
        result = build_oci_repository(_app(annotations={
            "app-operator.giantswarm.io/depends-on": "coredns",
        }), _catalog())
        assert "annotations" not in result["metadata"]

    def test_no_labels_when_app_has_none(self):
        assert "labels" not in build_oci_repository(_app(), _catalog())["metadata"]

    def test_labels_kept_when_not_in_blocklist(self):
        result = build_oci_repository(_app(labels={"env": "prod"}), _catalog())
        assert result["metadata"]["labels"] == {"env": "prod"}

    def test_blocked_label_removed(self):
        result = build_oci_repository(_app(labels={"app-operator.giantswarm.io/version": "1.0.0"}), _catalog())
        assert "labels" not in result["metadata"]

    def test_psp_status_label_removed(self):
        result = build_oci_repository(_app(labels={"policy.giantswarm.io/psp-status": "removed"}), _catalog())
        assert "labels" not in result["metadata"]

    def test_flux_label_removed(self):
        result = build_oci_repository(_app(labels={
            "kustomize.toolkit.fluxcd.io/namespace": "my-namespace",
        }), _catalog())
        assert "labels" not in result["metadata"]


# ---------------------------------------------------------------------------
# build_helm_release_and_helm_repo
# ---------------------------------------------------------------------------

def _helm_catalog(url="https://charts.example.io"):
    return {"spec": {"repositories": [{"type": "helm", "URL": url}]}}


class TestBuildHelmReleaseAndHelmRepo:
    def test_returns_two_documents(self):
        pair = build_helm_release_and_helm_repo(_app(), _helm_catalog())
        assert len(pair) == 2

    def test_first_doc_is_helm_repository(self):
        assert build_helm_release_and_helm_repo(_app(), _helm_catalog())[0]["kind"] == "HelmRepository"

    def test_helm_repository_api_version(self):
        assert build_helm_release_and_helm_repo(_app(), _helm_catalog())[0]["apiVersion"] == "source.toolkit.fluxcd.io/v1"

    def test_helm_repository_metadata_name(self):
        assert build_helm_release_and_helm_repo(_app(name="my-app"), _helm_catalog())[0]["metadata"]["name"] == "my-app"

    def test_helm_repository_metadata_namespace(self):
        assert build_helm_release_and_helm_repo(_app(namespace="giantswarm"), _helm_catalog())[0]["metadata"]["namespace"] == "giantswarm"

    def test_helm_repository_url_from_catalog(self):
        cat = _helm_catalog(url="https://charts.example.io/stable")
        assert build_helm_release_and_helm_repo(_app(), cat)[0]["spec"]["url"] == "https://charts.example.io/stable"

    def test_helm_repository_annotations_kept_when_not_in_blocklist(self):
        result = build_helm_release_and_helm_repo(_app(annotations={"custom.io/key": "value"}), _helm_catalog())
        assert result[0]["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_helm_repository_no_annotations_when_app_has_none(self):
        assert "annotations" not in build_helm_release_and_helm_repo(_app(), _helm_catalog())[0]["metadata"]

    def test_helm_repository_blocked_annotations_removed(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        }), _helm_catalog())
        assert "annotations" not in result[0]["metadata"]

    def test_helm_repository_mixed_annotations_only_blocklisted_removed(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "keep.me/key": "val",
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
        }), _helm_catalog())
        assert result[0]["metadata"]["annotations"] == {"keep.me/key": "val"}

    def test_helm_repository_flux_annotation_removed(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }), _helm_catalog())
        assert "annotations" not in result[0]["metadata"]

    def test_helm_repository_non_flux_annotation_kept_when_mixed_with_flux(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "custom.io/key": "value",
            "kustomize.toolkit.fluxcd.io/name": "my-kustomization",
        }), _helm_catalog())
        assert result[0]["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_helm_repository_latest_configmap_version_annotation_removed(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "app-operator.giantswarm.io/latest-configmap-version": "abc123",
        }), _helm_catalog())
        assert "annotations" not in result[0]["metadata"]

    def test_helm_repository_latest_secret_version_annotation_removed(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "app-operator.giantswarm.io/latest-secret-version": "abc123",
        }), _helm_catalog())
        assert "annotations" not in result[0]["metadata"]

    def test_helm_repository_depends_on_annotation_stripped_from_metadata(self):
        result = build_helm_release_and_helm_repo(_app(annotations={
            "app-operator.giantswarm.io/depends-on": "coredns",
        }), _helm_catalog())
        assert "annotations" not in result[0]["metadata"]

    def test_helm_repository_no_labels_when_app_has_none(self):
        assert "labels" not in build_helm_release_and_helm_repo(_app(), _helm_catalog())[0]["metadata"]

    def test_helm_repository_labels_kept_when_not_in_blocklist(self):
        result = build_helm_release_and_helm_repo(_app(labels={"env": "prod"}), _helm_catalog())
        assert result[0]["metadata"]["labels"] == {"env": "prod"}

    def test_helm_repository_blocked_label_removed(self):
        result = build_helm_release_and_helm_repo(_app(labels={"app-operator.giantswarm.io/version": "1.0.0"}), _helm_catalog())
        assert "labels" not in result[0]["metadata"]

    def test_helm_repository_psp_status_label_removed(self):
        result = build_helm_release_and_helm_repo(_app(labels={"policy.giantswarm.io/psp-status": "removed"}), _helm_catalog())
        assert "labels" not in result[0]["metadata"]

    def test_helm_repository_flux_label_removed(self):
        result = build_helm_release_and_helm_repo(_app(labels={
            "kustomize.toolkit.fluxcd.io/namespace": "my-namespace",
        }), _helm_catalog())
        assert "labels" not in result[0]["metadata"]

    def test_second_doc_is_helm_release(self):
        assert build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]["kind"] == "HelmRelease"

    def test_helm_release_has_no_chart_ref(self):
        hr = build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]
        assert "chartRef" not in hr["spec"]

    def test_helm_release_chart_spec_chart_from_spec_name(self):
        hr = build_helm_release_and_helm_repo(_app(spec_name="my-app"), _helm_catalog())[1]
        assert hr["spec"]["chart"]["spec"]["chart"] == "my-app"

    def test_helm_release_chart_spec_version_from_spec_version(self):
        hr = build_helm_release_and_helm_repo(_app(spec_extra={"version": "3.1.4"}), _helm_catalog())[1]
        assert hr["spec"]["chart"]["spec"]["version"] == "3.1.4"

    def test_helm_release_chart_source_ref_kind(self):
        hr = build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]
        assert hr["spec"]["chart"]["spec"]["sourceRef"]["kind"] == "HelmRepository"

    def test_helm_release_chart_source_ref_name_from_app_metadata(self):
        hr = build_helm_release_and_helm_repo(_app(name="my-app"), _helm_catalog())[1]
        assert hr["spec"]["chart"]["spec"]["sourceRef"]["name"] == "my-app"

    def test_helm_release_chart_source_ref_namespace_from_app_metadata(self):
        hr = build_helm_release_and_helm_repo(_app(namespace="giantswarm"), _helm_catalog())[1]
        assert hr["spec"]["chart"]["spec"]["sourceRef"]["namespace"] == "giantswarm"

    def test_helm_release_has_interval(self):
        assert build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]["spec"]["interval"] == "5m"

    def test_helm_release_has_install_remediation(self):
        hr = build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]
        assert hr["spec"]["install"]["remediation"]["retries"] == 10

    def test_helm_release_has_upgrade_remediation(self):
        hr = build_helm_release_and_helm_repo(_app(), _helm_catalog())[1]
        assert hr["spec"]["upgrade"]["remediation"]["remediateLastFailure"] is True

    def test_empty_version_raises(self):
        import pytest
        with pytest.raises(ValueError, match="spec.version"):
            build_helm_release_and_helm_repo(_app(spec_extra={"version": ""}), _helm_catalog())

    def test_missing_version_raises(self):
        import pytest
        app = _app()
        del app["spec"]["version"]
        with pytest.raises(ValueError, match="spec.version"):
            build_helm_release_and_helm_repo(app, _helm_catalog())

    def test_storage_fallback_used_when_no_repositories(self):
        cat = {"spec": {"storage": {"type": "helm", "URL": "https://fallback.example.io"}}}
        result = build_helm_release_and_helm_repo(_app(), cat)
        assert result[0]["spec"]["url"] == "https://fallback.example.io"

    def test_catalog_with_no_helm_type_raises(self):
        import pytest
        cat = {"spec": {"repositories": [{"type": "oci", "URL": "oci://example.io/charts"}]}}
        with pytest.raises(ValueError, match="no helm repository"):
            build_helm_release_and_helm_repo(_app(), cat)

    def test_non_helm_repo_before_helm_repo_uses_helm_url(self):
        cat = {"spec": {"repositories": [
            {"type": "s3", "URL": "s3://ignored"},
            {"type": "helm", "URL": "https://charts.example.io"},
        ]}}
        result = build_helm_release_and_helm_repo(_app(), cat)
        assert result[0]["spec"]["url"] == "https://charts.example.io"
