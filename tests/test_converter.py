from collections import OrderedDict

import yaml

from converter import convert


MINIMAL_APP_YAML = """\
apiVersion: application.giantswarm.io/v1alpha1
kind: App
metadata:
  name: my-app
  namespace: giantswarm
spec:
  name: my-app
  namespace: monitoring
  version: 1.2.3
"""


def _app_yaml(*, annotations=None, labels=None, spec_extra=None):
    meta = {"name": "my-app", "namespace": "giantswarm"}
    if annotations:
        meta["annotations"] = annotations
    if labels:
        meta["labels"] = labels
    spec = {"name": "my-app", "namespace": "monitoring", "version": "1.0.0"}
    if spec_extra:
        spec.update(spec_extra)
    return yaml.dump({
        "apiVersion": "application.giantswarm.io/v1alpha1",
        "kind": "App",
        "metadata": meta,
        "spec": spec,
    })


class TestConvert:
    def test_returns_two_documents(self):
        assert len(convert(MINIMAL_APP_YAML)) == 2

    def test_first_doc_is_oci_repository(self):
        oci = convert(MINIMAL_APP_YAML)[0]
        assert oci["kind"] == "OCIRepository"
        assert oci["apiVersion"] == "source.toolkit.fluxcd.io/v1beta2"

    def test_second_doc_is_helm_release(self):
        hr = convert(MINIMAL_APP_YAML)[1]
        assert hr["kind"] == "HelmRelease"
        assert hr["apiVersion"] == "helm.toolkit.fluxcd.io/v2"

    def test_oci_url_uses_spec_name(self):
        oci = convert(MINIMAL_APP_YAML)[0]
        assert oci["spec"]["url"] == "oci://gsoci.azurecr.io/charts/giantswarm/my-app"

    def test_metadata_propagated_to_both_docs(self):
        for doc in convert(MINIMAL_APP_YAML):
            assert doc["metadata"]["name"] == "my-app"
            assert doc["metadata"]["namespace"] == "giantswarm"

    def test_target_and_storage_namespace_from_spec_namespace(self):
        hr = convert(MINIMAL_APP_YAML)[1]
        assert hr["spec"]["targetNamespace"] == "monitoring"
        assert hr["spec"]["storageNamespace"] == "monitoring"

    def test_release_name_from_metadata_name(self):
        assert convert(MINIMAL_APP_YAML)[1]["spec"]["releaseName"] == "my-app"

    def test_chart_ref_points_to_oci_repository(self):
        hr = convert(MINIMAL_APP_YAML)[1]
        assert hr["spec"]["chartRef"] == OrderedDict([
            ("kind", "OCIRepository"),
            ("name", "my-app"),
            ("namespace", "giantswarm"),
        ])

    def test_no_values_from_when_no_config(self):
        assert "valuesFrom" not in convert(MINIMAL_APP_YAML)[1]["spec"]

    def test_values_from_set_when_config_present(self):
        yaml_str = _app_yaml(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        })
        hr = convert(yaml_str)[1]
        assert "valuesFrom" in hr["spec"]
        assert hr["spec"]["valuesFrom"][0]["name"] == "cluster-cm"

    def test_values_from_entries_have_no_namespace(self):
        yaml_str = _app_yaml(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        })
        assert "namespace" not in convert(yaml_str)[1]["spec"]["valuesFrom"][0]

    def test_annotations_kept_when_not_in_blocklist(self):
        yaml_str = _app_yaml(annotations={"custom.io/key": "value"})
        assert convert(yaml_str)[1]["metadata"]["annotations"] == {"custom.io/key": "value"}

    def test_blocked_annotations_removed(self):
        yaml_str = _app_yaml(annotations={
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        })
        assert "annotations" not in convert(yaml_str)[1]["metadata"]

    def test_mixed_annotations_only_blocklisted_ones_removed(self):
        yaml_str = _app_yaml(annotations={
            "keep.me/key": "val",
            "chart-operator.giantswarm.io/force-helm-upgrade": "true",
            "app-operator.giantswarm.io/paused": "true",
        })
        assert convert(yaml_str)[1]["metadata"]["annotations"] == {"keep.me/key": "val"}

    def test_no_annotations_in_app_produces_no_annotations_key(self):
        assert "annotations" not in convert(MINIMAL_APP_YAML)[1]["metadata"]

    def test_labels_kept_when_not_in_blocklist(self):
        yaml_str = _app_yaml(labels={"env": "prod"})
        assert convert(yaml_str)[1]["metadata"]["labels"] == {"env": "prod"}

    def test_blocked_label_removed(self):
        yaml_str = _app_yaml(labels={"app-operator.giantswarm.io/version": "1.0.0"})
        assert "labels" not in convert(yaml_str)[1]["metadata"]

    def test_mixed_labels_only_blocklisted_ones_removed(self):
        yaml_str = _app_yaml(labels={
            "team": "honeybadger",
            "app-operator.giantswarm.io/version": "1.0.0",
        })
        assert convert(yaml_str)[1]["metadata"]["labels"] == {"team": "honeybadger"}

    def test_no_labels_in_app_produces_no_labels_key(self):
        assert "labels" not in convert(MINIMAL_APP_YAML)[1]["metadata"]
