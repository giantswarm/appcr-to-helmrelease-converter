from unittest.mock import patch

from resolver import Resolution, resolve


_APP_NS = "org-acme"

def _app(spec=None):
    return {"metadata": {"namespace": _APP_NS}, "spec": spec or {}}


def _cm(name, keys):
    return {"data": {k: "x" for k in keys}}


def _secret(name, keys):
    return {"data": {k: "x" for k in keys}}


class TestResolveNoRefs:
    def test_empty_spec_returns_empty_resolution(self):
        result = resolve(_app(), {})
        assert isinstance(result, Resolution)
        assert result.key_overrides == {}


class TestResolveMultipleKeys:
    def test_multiple_keys_prompts_user(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", ["values.yaml", "configmap-values.yaml"])}
        with patch("click.prompt", return_value="configmap-values.yaml") as mock_prompt:
            result = resolve(app, refs)
        mock_prompt.assert_called_once()
        assert result.key_overrides == {("ConfigMap", "cm"): "configmap-values.yaml"}

    def test_multiple_keys_prompt_receives_all_keys_as_choices(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        keys = ["alpha.yaml", "beta.yaml", "gamma.yaml"]
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", keys)}
        with patch("click.prompt", return_value="beta.yaml") as mock_prompt:
            resolve(app, refs)
        call_kwargs = mock_prompt.call_args
        choice_arg = call_kwargs.kwargs.get("type") or call_kwargs.args[1]
        assert set(choice_arg.choices) == set(keys)

    def test_single_key_does_not_prompt(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", ["configmap-values.yaml"])}
        with patch("click.prompt") as mock_prompt:
            resolve(app, refs)
        mock_prompt.assert_not_called()


class TestResolveValuesYamlDefault:
    def test_values_yaml_key_stored_as_string(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", ["values.yaml"])}
        result = resolve(app, refs)
        assert result.key_overrides == {("ConfigMap", "cm"): "values.yaml"}


class TestResolveSkips:
    def test_none_resource_skipped_no_override(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        refs = {("ConfigMap", "cm", _APP_NS): None}
        result = resolve(app, refs)
        assert ("ConfigMap", "cm") not in result.key_overrides

    def test_same_kind_name_deduplicated(self):
        app = _app({
            "config": {"configMap": {"name": "cm", "namespace": _APP_NS}},
            "userConfig": {"configMap": {"name": "cm", "namespace": _APP_NS}},
        })
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", ["values.yaml"])}
        with patch("click.prompt") as mock_prompt:
            result = resolve(app, refs)
        mock_prompt.assert_not_called()
        assert list(result.key_overrides.keys()).count(("ConfigMap", "cm")) == 1


class TestResolveSingleKey:
    def test_single_key_configmap_auto_resolves(self):
        app = _app({"config": {"configMap": {"name": "cm", "namespace": _APP_NS}}})
        refs = {("ConfigMap", "cm", _APP_NS): _cm("cm", ["configmap-values.yaml"])}
        result = resolve(app, refs)
        assert result.key_overrides == {("ConfigMap", "cm"): "configmap-values.yaml"}

    def test_single_key_secret_auto_resolves(self):
        app = _app({"config": {"secret": {"name": "s", "namespace": _APP_NS}}})
        refs = {("Secret", "s", _APP_NS): _secret("s", ["secret-values.yaml"])}
        result = resolve(app, refs)
        assert result.key_overrides == {("Secret", "s"): "secret-values.yaml"}
