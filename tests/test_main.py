import io
from collections import OrderedDict
from unittest.mock import patch

import yaml

from main import (
    ReferenceWithPriority,
    calculate_values_from,
    convert,
    main,
    to_reference_with_priority,
)


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


# ---------------------------------------------------------------------------
# to_reference_with_priority
# ---------------------------------------------------------------------------

class TestToReferenceWithPriority:
    def test_none_returns_none(self):
        assert to_reference_with_priority(None, "ConfigMap", 50) is None

    def test_empty_dict_returns_none(self):
        assert to_reference_with_priority({}, "ConfigMap", 50) is None

    def test_returns_namedtuple(self):
        result = to_reference_with_priority({"name": "cm"}, "ConfigMap", 50)
        assert isinstance(result, ReferenceWithPriority)

    def test_uses_default_priority(self):
        result = to_reference_with_priority({"name": "cm"}, "ConfigMap", 75)
        assert result.priority == 75

    def test_explicit_priority_overrides_default(self):
        # App CR spec.extraConfigs[].priority overrides the caller-supplied default
        result = to_reference_with_priority({"name": "cm", "priority": 30}, "ConfigMap", 75)
        assert result.priority == 30

    def test_name_is_set(self):
        result = to_reference_with_priority({"name": "my-cm"}, "ConfigMap", 50)
        assert result.reference["name"] == "my-cm"

    def test_missing_name_defaults_to_empty_string(self):
        result = to_reference_with_priority({"priority": 10}, "ConfigMap", 50)
        assert result.reference["name"] == ""

    def test_configmap_values_key(self):
        # GiantSwarm migration convention: ConfigMap data key is configmap-values.yaml
        result = to_reference_with_priority({"name": "cm"}, "ConfigMap", 50)
        assert result.reference["valuesKey"] == "configmap-values.yaml"

    def test_secret_values_key(self):
        # GiantSwarm migration convention: Secret data key is secret-values.yaml
        result = to_reference_with_priority({"name": "s"}, "Secret", 50)
        assert result.reference["valuesKey"] == "secret-values.yaml"

    def test_kind_camelcase_configmap_values_key(self):
        # App CR spec.extraConfigs[].kind defaults to "configMap" (camelCase per docs)
        result = to_reference_with_priority({"name": "cm"}, "configMap", 50)
        assert result.reference["valuesKey"] == "configmap-values.yaml"

    def test_kind_camelcase_configmap_output_kind(self):
        result = to_reference_with_priority({"name": "cm"}, "configMap", 50)
        assert result.reference["kind"] == "ConfigMap"

    def test_kind_lowercase_secret_values_key(self):
        result = to_reference_with_priority({"name": "s"}, "secret", 50)
        assert result.reference["valuesKey"] == "secret-values.yaml"

    def test_kind_already_correct_case_preserved(self):
        result = to_reference_with_priority({"name": "s"}, "Secret", 50)
        assert result.reference["kind"] == "Secret"

    def test_namespace_field_not_propagated_to_output(self):
        # App CR config refs carry a namespace field (spec.config.configMap.namespace,
        # spec.userConfig.*.namespace, spec.extraConfigs[].namespace) but HelmRelease
        # valuesFrom entries don't have a namespace — the referenced objects are expected
        # to be in the same namespace as the HelmRelease.
        result = to_reference_with_priority({"name": "cm", "namespace": "giantswarm"}, "ConfigMap", 50)
        assert "namespace" not in result.reference

    def test_output_reference_has_exactly_kind_name_valueskey(self):
        result = to_reference_with_priority({"name": "cm", "namespace": "ns", "priority": 10}, "ConfigMap", 50)
        assert set(result.reference.keys()) == {"kind", "name", "valuesKey"}


# ---------------------------------------------------------------------------
# calculate_values_from
# ---------------------------------------------------------------------------

class TestCalculateValuesFrom:
    def _app(self, spec):
        return {"spec": spec}

    def test_empty_spec_returns_empty_list(self):
        assert calculate_values_from(self._app({})) == []

    def test_cluster_configmap(self):
        app = self._app({"config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}}})
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["kind"] == "ConfigMap"
        assert result[0]["name"] == "cluster-cm"

    def test_cluster_secret(self):
        app = self._app({"config": {"secret": {"name": "cluster-s", "namespace": "giantswarm"}}})
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["kind"] == "Secret"
        assert result[0]["name"] == "cluster-s"

    def test_user_configmap(self):
        app = self._app({"userConfig": {"configMap": {"name": "user-cm", "namespace": "giantswarm"}}})
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["kind"] == "ConfigMap"
        assert result[0]["name"] == "user-cm"

    def test_user_secret(self):
        app = self._app({"userConfig": {"secret": {"name": "user-s", "namespace": "giantswarm"}}})
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["kind"] == "Secret"
        assert result[0]["name"] == "user-s"

    def test_config_namespace_not_propagated(self):
        # spec.config.configMap.namespace is defined in the App CR schema but is not
        # carried through to HelmRelease valuesFrom entries.
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "giantswarm"}}})
        assert "namespace" not in calculate_values_from(app)[0]

    def test_extra_configs_default_kind_is_configmap(self):
        # When kind is absent the App CR schema defaults to configMap
        result = calculate_values_from(self._app({"extraConfigs": [{"name": "extra", "namespace": "ns"}]}))
        assert len(result) == 1
        assert result[0]["kind"] == "ConfigMap"

    def test_extra_configs_kind_camelcase_configmap(self):
        # App CR docs specify kind as "configMap" (camelCase)
        app = self._app({"extraConfigs": [{"name": "extra", "kind": "configMap", "namespace": "ns"}]})
        result = calculate_values_from(app)
        assert result[0]["kind"] == "ConfigMap"

    def test_extra_configs_kind_secret(self):
        app = self._app({"extraConfigs": [{"name": "extra", "kind": "Secret", "namespace": "ns"}]})
        assert calculate_values_from(app)[0]["kind"] == "Secret"

    def test_extra_configs_empty_entry_skipped(self):
        result = calculate_values_from(self._app({"extraConfigs": [{}]}))
        assert result == []

    def test_extra_configs_namespace_not_propagated(self):
        # spec.extraConfigs[].namespace is required in the App CR schema but is not
        # carried through to HelmRelease valuesFrom entries.
        app = self._app({"extraConfigs": [{"name": "extra", "namespace": "some-ns"}]})
        assert "namespace" not in calculate_values_from(app)[0]

    def test_sort_configmap_before_secret(self):
        app = self._app({
            "config": {
                "configMap": {"name": "cm", "namespace": "ns"},
                "secret": {"name": "s", "namespace": "ns"},
            },
        })
        result = calculate_values_from(app)
        assert result[0]["kind"] == "ConfigMap"
        assert result[1]["kind"] == "Secret"

    def test_priority_ordering_extra_cluster_user(self):
        # Default priorities: extraConfigs=25, spec.config=50, spec.userConfig=100
        app = self._app({
            "config": {"configMap": {"name": "cluster-cm", "namespace": "ns"}},
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "extra-cm", "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        assert [r["name"] for r in result] == ["extra-cm", "cluster-cm", "user-cm"]

    def test_extra_config_priority_boundary_min(self):
        # Priority range per docs: 1–150 inclusive
        app = self._app({"extraConfigs": [{"name": "low", "priority": 1, "namespace": "ns"}]})
        result = calculate_values_from(app)
        assert result[0]["name"] == "low"

    def test_extra_config_priority_boundary_max(self):
        # Priority 150 is the documented maximum, higher than user config (100)
        app = self._app({
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "override", "priority": 150, "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        assert result[-1]["name"] == "override"

    def test_extra_config_interleaved_between_cluster_and_user(self):
        # An extraConfig with priority 75 should be applied after cluster (50)
        # but before user (100), allowing fine-grained layering.
        app = self._app({
            "config": {"configMap": {"name": "cluster-cm", "namespace": "ns"}},
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "mid", "priority": 75, "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        names = [r["name"] for r in result]
        assert names == ["cluster-cm", "mid", "user-cm"]

    def test_extra_configs_same_priority_list_order_preserved(self):
        # When two extraConfigs share the same priority, the item later in the list
        # wins (per docs: "items lower on the list override those higher on the list").
        # Stable sort preserves insertion order, so list order is maintained.
        app = self._app({
            "extraConfigs": [
                {"name": "first", "priority": 25, "namespace": "ns"},
                {"name": "second", "priority": 25, "namespace": "ns"},
            ]
        })
        result = calculate_values_from(app)
        assert [r["name"] for r in result] == ["first", "second"]

    def test_all_sources_five_entries(self):
        app = self._app({
            "config": {
                "configMap": {"name": "c-cm", "namespace": "ns"},
                "secret": {"name": "c-s", "namespace": "ns"},
            },
            "userConfig": {
                "configMap": {"name": "u-cm", "namespace": "ns"},
                "secret": {"name": "u-s", "namespace": "ns"},
            },
            "extraConfigs": [{"name": "e-cm", "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        assert len(result) == 5
        # All ConfigMaps come before all Secrets (sorted by kind ascending)
        first_secret = next(i for i, r in enumerate(result) if r["kind"] == "Secret")
        assert all(r["kind"] == "ConfigMap" for r in result[:first_secret])
        assert all(r["kind"] == "Secret" for r in result[first_secret:])


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

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
        # App CR configMap/secret refs carry a namespace field, but HelmRelease
        # valuesFrom entries do not — the referenced objects must be co-located
        # with the HelmRelease.
        yaml_str = _app_yaml(spec_extra={
            "config": {"configMap": {"name": "cluster-cm", "namespace": "giantswarm"}},
        })
        hr = convert(yaml_str)[1]
        assert "namespace" not in hr["spec"]["valuesFrom"][0]

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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def _run_main(self, yaml_str):
        lines = iter(yaml_str.splitlines(keepends=True))
        with patch("fileinput.input", return_value=lines):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                main()
                return mock_out.getvalue()

    def test_output_starts_with_separator(self):
        assert self._run_main(MINIMAL_APP_YAML).startswith("---\n")

    def test_output_contains_two_yaml_documents(self):
        docs = list(yaml.safe_load_all(self._run_main(MINIMAL_APP_YAML)))
        assert len(docs) == 2

    def test_output_oci_repository_first(self):
        docs = list(yaml.safe_load_all(self._run_main(MINIMAL_APP_YAML)))
        assert docs[0]["kind"] == "OCIRepository"

    def test_output_helm_release_second(self):
        docs = list(yaml.safe_load_all(self._run_main(MINIMAL_APP_YAML)))
        assert docs[1]["kind"] == "HelmRelease"
