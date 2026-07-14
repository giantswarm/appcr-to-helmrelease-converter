from collections import OrderedDict

from converter import convert


MINIMAL_APP = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "App",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {"name": "my-app", "namespace": "monitoring", "version": "1.2.3"},
}

MINIMAL_CATALOG = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "Catalog",
    "metadata": {"name": "giantswarm", "namespace": "giantswarm"},
    "spec": {"repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/charts/giantswarm"}]},
}


def _app_dict(*, annotations=None, labels=None, spec_extra=None):
    meta = {"name": "my-app", "namespace": "giantswarm"}
    if annotations:
        meta["annotations"] = annotations
    if labels:
        meta["labels"] = labels
    spec = {"name": "my-app", "namespace": "monitoring", "version": "1.0.0"}
    if spec_extra:
        spec.update(spec_extra)
    return {
        "apiVersion": "application.giantswarm.io/v1alpha1",
        "kind": "App",
        "metadata": meta,
        "spec": spec,
    }


class TestConvert:
    def test_returns_two_documents(self):
        assert len(convert(MINIMAL_APP, MINIMAL_CATALOG)) == 2

    def test_first_doc_is_oci_repository(self):
        oci = convert(MINIMAL_APP, MINIMAL_CATALOG)[0]
        assert oci["kind"] == "OCIRepository"
        assert oci["apiVersion"] == "source.toolkit.fluxcd.io/v1beta2"

    def test_second_doc_is_helm_release(self):
        hr = convert(MINIMAL_APP, MINIMAL_CATALOG)[1]
        assert hr["kind"] == "HelmRelease"
        assert hr["apiVersion"] == "helm.toolkit.fluxcd.io/v2"

    def test_oci_url_uses_catalog_base_and_spec_name(self):
        oci = convert(MINIMAL_APP, MINIMAL_CATALOG)[0]
        assert oci["spec"]["url"] == "oci://gsoci.azurecr.io/charts/giantswarm/my-app"

    def test_metadata_propagated_to_both_docs(self):
        for doc in convert(MINIMAL_APP, MINIMAL_CATALOG):
            assert doc["metadata"]["name"] == "my-app"
            assert doc["metadata"]["namespace"] == "giantswarm"

    def test_target_and_storage_namespace_from_spec_namespace(self):
        hr = convert(MINIMAL_APP, MINIMAL_CATALOG)[1]
        assert hr["spec"]["targetNamespace"] == "monitoring"
        assert hr["spec"]["storageNamespace"] == "monitoring"

    def test_release_name_from_metadata_name(self):
        assert convert(MINIMAL_APP, MINIMAL_CATALOG)[1]["spec"]["releaseName"] == "my-app"

    def test_chart_ref_points_to_oci_repository(self):
        hr = convert(MINIMAL_APP, MINIMAL_CATALOG)[1]
        assert hr["spec"]["chartRef"] == OrderedDict([
            ("kind", "OCIRepository"),
            ("name", "my-app"),
            ("namespace", "giantswarm"),
        ])

    def test_no_values_from_when_no_config(self):
        assert "valuesFrom" not in convert(MINIMAL_APP, MINIMAL_CATALOG)[1]["spec"]

    def test_values_from_set_when_config_present(self):
        app = _app_dict(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        })
        hr = convert(app, MINIMAL_CATALOG)[1]
        assert "valuesFrom" in hr["spec"]
        assert hr["spec"]["valuesFrom"][0]["name"] == "cluster-cm"

    def test_values_from_entries_have_no_namespace(self):
        app = _app_dict(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        })
        assert "namespace" not in convert(app, MINIMAL_CATALOG)[1]["spec"]["valuesFrom"][0]

    def test_annotations_kept_when_not_in_blocklist(self):
        app = _app_dict(annotations={"custom.io/key": "value"})
        assert convert(app, MINIMAL_CATALOG)[1]["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_blocked_annotations_removed(self):
        app = _app_dict(annotations={
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        })
        assert "annotations" not in convert(app, MINIMAL_CATALOG)[1]["metadata"]

    def test_mixed_annotations_only_blocklisted_ones_removed(self):
        app = _app_dict(annotations={
            "keep.me/key": "val",
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        })
        assert convert(app, MINIMAL_CATALOG)[1]["metadata"]["annotations"] == {"keep.me/key": "val"}

    def test_no_annotations_in_app_produces_no_annotations_key(self):
        assert "annotations" not in convert(MINIMAL_APP, MINIMAL_CATALOG)[1]["metadata"]

    def test_labels_kept_when_not_in_blocklist(self):
        app = _app_dict(labels={"env": "prod"})
        assert convert(app, MINIMAL_CATALOG)[1]["metadata"]["labels"] == {"env": "prod"}

    def test_blocked_label_removed(self):
        app = _app_dict(labels={"app-operator.giantswarm.io/version": "1.0.0"})
        assert "labels" not in convert(app, MINIMAL_CATALOG)[1]["metadata"]

    def test_psp_status_label_removed(self):
        app = _app_dict(labels={"policy.giantswarm.io/psp-status": "removed"})
        assert "labels" not in convert(app, MINIMAL_CATALOG)[1]["metadata"]

    def test_mixed_labels_only_blocklisted_ones_removed(self):
        app = _app_dict(labels={
            "team": "honeybadger",
            "app-operator.giantswarm.io/version": "1.0.0",
        })
        assert convert(app, MINIMAL_CATALOG)[1]["metadata"]["labels"] == {"team": "honeybadger"}

    def test_no_labels_in_app_produces_no_labels_key(self):
        assert "labels" not in convert(MINIMAL_APP, MINIMAL_CATALOG)[1]["metadata"]


HELM_CATALOG = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "Catalog",
    "metadata": {"name": "example", "namespace": "giantswarm"},
    "spec": {"repositories": [{"type": "helm", "URL": "https://charts.example.io"}]},
}


class TestConvertPathB:
    def test_returns_two_documents(self):
        assert len(convert(MINIMAL_APP, HELM_CATALOG)) == 2

    def test_first_doc_is_helm_repository(self):
        assert convert(MINIMAL_APP, HELM_CATALOG)[0]["kind"] == "HelmRepository"

    def test_second_doc_is_helm_release(self):
        assert convert(MINIMAL_APP, HELM_CATALOG)[1]["kind"] == "HelmRelease"

    def test_helm_release_has_chart_spec_not_chart_ref(self):
        hr = convert(MINIMAL_APP, HELM_CATALOG)[1]
        assert "chart" in hr["spec"]
        assert "chartRef" not in hr["spec"]

    def test_oci_preferred_when_both_types_present(self):
        both = {
            "spec": {"repositories": [
                {"type": "helm", "URL": "https://charts.example.io"},
                {"type": "oci", "URL": "oci://example.io/charts"},
            ]}
        }
        assert convert(MINIMAL_APP, both)[0]["kind"] == "OCIRepository"
