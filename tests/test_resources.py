from collections import OrderedDict

from converter.resources import build_oci_repository, build_helm_release


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

    def test_no_labels_when_app_has_none(self):
        assert "labels" not in build_helm_release(_app())["metadata"]

    def test_labels_kept_when_not_in_blocklist(self):
        result = build_helm_release(_app(labels={"env": "prod"}))
        assert result["metadata"]["labels"] == {"env": "prod"}

    def test_blocked_label_removed(self):
        result = build_helm_release(_app(labels={"app-operator.giantswarm.io/version": "1.0.0"}))
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
        assert build_oci_repository(_app())["kind"] == "OCIRepository"

    def test_api_version(self):
        assert build_oci_repository(_app())["apiVersion"] == "source.toolkit.fluxcd.io/v1beta2"

    def test_metadata_name(self):
        assert build_oci_repository(_app(name="my-app"))["metadata"]["name"] == "my-app"

    def test_metadata_namespace(self):
        assert build_oci_repository(_app(namespace="giantswarm"))["metadata"]["namespace"] == "giantswarm"

    def test_url_uses_spec_name(self):
        assert build_oci_repository(_app(spec_name="kong-app"))["spec"]["url"] == \
            "oci://gsoci.azurecr.io/charts/giantswarm/kong-app"

    def test_interval(self):
        assert build_oci_repository(_app())["spec"]["interval"] == "10m"

    def test_provider(self):
        assert build_oci_repository(_app())["spec"]["provider"] == "generic"

    def test_ref_tag_from_spec_version(self):
        assert build_oci_repository(_app())["spec"]["ref"]["tag"] == "1.0.0"

    def test_ref_tag_reflects_spec_version_value(self):
        assert build_oci_repository(_app(spec_extra={"version": "2.3.4"}))["spec"]["ref"]["tag"] == "2.3.4"

    def test_empty_version_raises(self):
        import pytest
        with pytest.raises(ValueError, match="spec.version"):
            build_oci_repository(_app(spec_extra={"version": ""}))

    def test_missing_version_raises(self):
        import pytest
        app = _app()
        del app["spec"]["version"]
        with pytest.raises(ValueError, match="spec.version"):
            build_oci_repository(app)
