from collections import OrderedDict

from converter.values_from import ReferenceWithPriority, calculate_values_from, to_reference_with_priority
from resolver import Resolution


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
        result = to_reference_with_priority({"name": "cm", "priority": 30}, "ConfigMap", 75)
        assert result.priority == 30

    def test_name_is_set(self):
        result = to_reference_with_priority({"name": "my-cm"}, "ConfigMap", 50)
        assert result.reference["name"] == "my-cm"

    def test_empty_name_returns_none(self):
        assert to_reference_with_priority({"name": ""}, "ConfigMap", 50) is None

    def test_missing_name_returns_none(self):
        assert to_reference_with_priority({"priority": 10}, "ConfigMap", 50) is None

    def test_empty_name_with_namespace_returns_none(self):
        assert to_reference_with_priority({"name": "", "namespace": "org-x"}, "Secret", 50) is None

    def test_configmap_values_key(self):
        result = to_reference_with_priority({"name": "cm"}, "ConfigMap", 50)
        assert result.reference["valuesKey"] == "configmap-values.yaml"

    def test_secret_values_key(self):
        result = to_reference_with_priority({"name": "s"}, "Secret", 50)
        assert result.reference["valuesKey"] == "secret-values.yaml"

    def test_kind_camelcase_configmap_values_key(self):
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
        result = to_reference_with_priority({"name": "cm", "namespace": "giantswarm"}, "ConfigMap", 50)
        assert "namespace" not in result.reference

    def test_output_reference_has_exactly_kind_name_valueskey(self):
        result = to_reference_with_priority({"name": "cm", "namespace": "ns", "priority": 10}, "ConfigMap", 50)
        assert set(result.reference.keys()) == {"kind", "name", "valuesKey"}


class TestCalculateValuesFrom:
    def _app(self, spec, namespace=None):
        app = {"spec": spec}
        if namespace is not None:
            app["metadata"] = {"namespace": namespace}
        return app

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
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "giantswarm"}}})
        assert "namespace" not in calculate_values_from(app)[0]

    def test_extra_configs_default_kind_is_configmap(self):
        result = calculate_values_from(self._app({"extraConfigs": [{"name": "extra", "namespace": "ns"}]}))
        assert len(result) == 1
        assert result[0]["kind"] == "ConfigMap"

    def test_extra_configs_kind_camelcase_configmap(self):
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
        app = self._app({"extraConfigs": [{"name": "extra", "namespace": "some-ns"}]})
        assert "namespace" not in calculate_values_from(app)[0]

    def test_extra_configs_psp_removal_patch_configmap_dropped(self):
        app = self._app({"extraConfigs": [{"name": "psp-removal-patch", "namespace": "ns"}]}, namespace="ns")
        assert calculate_values_from(app) == []

    def test_extra_configs_psp_removal_patch_secret_kind_not_dropped(self):
        app = self._app(
            {"extraConfigs": [{"name": "psp-removal-patch", "kind": "Secret", "namespace": "ns"}]}, namespace="ns"
        )
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["kind"] == "Secret"

    def test_extra_configs_psp_removal_patch_suffixed_name_dropped(self):
        app = self._app(
            {"extraConfigs": [{"name": "psp-removal-patch-datadog", "namespace": "ns"}]}, namespace="ns"
        )
        assert calculate_values_from(app) == []

    def test_extra_configs_psp_removal_patch_different_namespace_not_dropped(self):
        app = self._app(
            {"extraConfigs": [{"name": "psp-removal-patch", "namespace": "other-ns"}]}, namespace="ns"
        )
        result = calculate_values_from(app)
        assert len(result) == 1
        assert result[0]["name"] == "psp-removal-patch"

    def test_extra_configs_psp_removal_patch_missing_namespace_defaults_to_app_namespace(self):
        app = self._app({"extraConfigs": [{"name": "psp-removal-patch"}]}, namespace="ns")
        assert calculate_values_from(app) == []

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
        app = self._app({
            "config": {"configMap": {"name": "cluster-cm", "namespace": "ns"}},
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "extra-cm", "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        assert [r["name"] for r in result] == ["extra-cm", "cluster-cm", "user-cm"]

    def test_extra_config_priority_boundary_min(self):
        app = self._app({"extraConfigs": [{"name": "low", "priority": 1, "namespace": "ns"}]})
        result = calculate_values_from(app)
        assert result[0]["name"] == "low"

    def test_extra_config_priority_boundary_max(self):
        app = self._app({
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "override", "priority": 150, "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        assert result[-1]["name"] == "override"

    def test_extra_config_interleaved_between_cluster_and_user(self):
        app = self._app({
            "config": {"configMap": {"name": "cluster-cm", "namespace": "ns"}},
            "userConfig": {"configMap": {"name": "user-cm", "namespace": "ns"}},
            "extraConfigs": [{"name": "mid", "priority": 75, "namespace": "ns"}],
        })
        result = calculate_values_from(app)
        names = [r["name"] for r in result]
        assert names == ["cluster-cm", "mid", "user-cm"]

    def test_extra_configs_same_priority_list_order_preserved(self):
        app = self._app({
            "extraConfigs": [
                {"name": "first", "priority": 25, "namespace": "ns"},
                {"name": "second", "priority": 25, "namespace": "ns"},
            ]
        })
        result = calculate_values_from(app)
        assert [r["name"] for r in result] == ["first", "second"]

    def test_resolution_none_key_omits_values_key(self):
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "ns"}}})
        resolution = Resolution(key_overrides={("ConfigMap", "cm"): None})
        result = calculate_values_from(app, resolution)
        assert "valuesKey" not in result[0]

    def test_resolution_values_yaml_key_omits_values_key(self):
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "ns"}}})
        resolution = Resolution(key_overrides={("ConfigMap", "cm"): "values.yaml"})
        result = calculate_values_from(app, resolution)
        assert "valuesKey" not in result[0]

    def test_resolution_overrides_configmap_values_key(self):
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "ns"}}})
        resolution = Resolution(key_overrides={("ConfigMap", "cm"): "my-values.yaml"})
        result = calculate_values_from(app, resolution)
        assert result[0]["valuesKey"] == "my-values.yaml"

    def test_resolution_optional_marks_reference_optional(self):
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "ns"}}})
        resolution = Resolution(
            key_overrides={("ConfigMap", "cm"): None},
            optional={("ConfigMap", "cm")},
        )
        result = calculate_values_from(app, resolution)
        assert result[0]["optional"] is True
        assert "valuesKey" not in result[0]

    def test_resolution_not_optional_omits_optional_field(self):
        app = self._app({"config": {"configMap": {"name": "cm", "namespace": "ns"}}})
        resolution = Resolution(key_overrides={("ConfigMap", "cm"): "values.yaml"})
        result = calculate_values_from(app, resolution)
        assert "optional" not in result[0]

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
        first_secret = next(i for i, r in enumerate(result) if r["kind"] == "Secret")
        assert all(r["kind"] == "ConfigMap" for r in result[:first_secret])
        assert all(r["kind"] == "Secret" for r in result[first_secret:])
