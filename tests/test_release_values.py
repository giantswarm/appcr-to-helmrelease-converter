import base64
from unittest.mock import MagicMock

import pytest
import yaml
from kubernetes.client.exceptions import ApiException

from migrator import MigratorError
from migrator.release_values import (
    ValuesSource,
    find_release_version_sources,
    get_helm_release,
    remove_release_version,
)

_WITH_VERSION = "global:\n  release:\n    version: 34.0.0\n  metadata:\n    name: mycluster\n"
_WITHOUT_VERSION = "global:\n  metadata:\n    name: mycluster\n"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _obj(data):
    obj = MagicMock()
    obj.data = data
    return obj


def _core(configmaps=None, secrets=None, error=None):
    core = MagicMock()
    configmaps, secrets = configmaps or {}, secrets or {}

    def _reader(store):
        def _read(name, namespace):
            if error is not None:
                raise error
            if name not in store:
                raise ApiException(status=404)
            return _obj(store[name])
        return _read

    core.read_namespaced_config_map.side_effect = _reader(configmaps)
    core.read_namespaced_secret.side_effect = _reader(secrets)
    return core


def _hr(*values_from):
    return {"metadata": {"name": "mycluster", "namespace": "org-acme"},
            "spec": {"valuesFrom": list(values_from)}}


class TestGetHelmRelease:
    _APP = {"metadata": {"name": "mycluster", "namespace": "org-acme"}}

    def test_returns_helm_release(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {"kind": "HelmRelease"}
        assert get_helm_release(api, self._APP) == {"kind": "HelmRelease"}
        assert api.get_namespaced_custom_object.call_args.kwargs["name"] == "mycluster"
        assert api.get_namespaced_custom_object.call_args.kwargs["namespace"] == "org-acme"

    def test_error_is_migrator_error(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=500, reason="boom")
        with pytest.raises(MigratorError, match="failed to fetch HelmRelease org-acme/mycluster: boom"):
            get_helm_release(api, self._APP)


class TestFindReleaseVersionSources:
    def test_finds_configmap_with_value(self):
        core = _core(configmaps={"user-values": {"values": _WITH_VERSION}})
        found = find_release_version_sources(
            core, _hr({"kind": "ConfigMap", "name": "user-values", "valuesKey": "values"}))
        assert found == [ValuesSource("ConfigMap", "user-values", "org-acme", "values")]

    def test_finds_secret_with_value(self):
        core = _core(secrets={"user-secret": {"values": _b64(_WITH_VERSION)}})
        found = find_release_version_sources(
            core, _hr({"kind": "Secret", "name": "user-secret", "valuesKey": "values"}))
        assert found == [ValuesSource("Secret", "user-secret", "org-acme", "values")]

    def test_defaults_to_values_yaml_key_and_configmap_kind(self):
        core = _core(configmaps={"cm": {"values.yaml": _WITH_VERSION}})
        found = find_release_version_sources(core, _hr({"name": "cm"}))
        assert found == [ValuesSource("ConfigMap", "cm", "org-acme", "values.yaml")]

    def test_ignores_sources_without_value(self):
        core = _core(configmaps={"cm": {"values": _WITHOUT_VERSION}, "other": {"values": "foo: bar\n"}})
        found = find_release_version_sources(core, _hr(
            {"kind": "ConfigMap", "name": "cm", "valuesKey": "values"},
            {"kind": "ConfigMap", "name": "other", "valuesKey": "values"},
        ))
        assert found == []

    def test_ignores_release_block_without_version(self):
        core = _core(configmaps={"cm": {"values": "global:\n  release: {}\n"}})
        assert find_release_version_sources(core, _hr({"name": "cm", "valuesKey": "values"})) == []

    def test_ignores_non_mapping_global_and_release(self):
        core = _core(configmaps={"a": {"values": "global: x\n"}, "b": {"values": "global:\n  release: x\n"},
                                 "c": {"values": "- a list\n"}})
        found = find_release_version_sources(core, _hr(
            {"name": "a", "valuesKey": "values"}, {"name": "b", "valuesKey": "values"},
            {"name": "c", "valuesKey": "values"},
        ))
        assert found == []

    def test_skips_target_path_entries(self):
        core = _core(configmaps={"cm": {"values": _WITH_VERSION}})
        found = find_release_version_sources(
            core, _hr({"name": "cm", "valuesKey": "values", "targetPath": "foo"}))
        assert found == []
        core.read_namespaced_config_map.assert_not_called()

    def test_skips_missing_resource_and_missing_key(self):
        core = _core(configmaps={"cm": {"other": _WITH_VERSION}, "empty": None})
        found = find_release_version_sources(core, _hr(
            {"name": "gone", "valuesKey": "values"}, {"name": "cm", "valuesKey": "values"},
            {"name": "empty", "valuesKey": "values"},
        ))
        assert found == []

    def test_no_values_from(self):
        assert find_release_version_sources(_core(), {"metadata": {"namespace": "ns"}, "spec": {}}) == []

    def test_read_error_is_migrator_error(self):
        core = _core(error=ApiException(status=403, reason="Forbidden"))
        with pytest.raises(MigratorError, match="failed to fetch ConfigMap org-acme/cm: Forbidden"):
            find_release_version_sources(core, _hr({"name": "cm"}))

    def test_invalid_yaml_is_migrator_error(self):
        core = _core(configmaps={"cm": {"values.yaml": "a: [unclosed"}})
        with pytest.raises(MigratorError, match="does not hold valid YAML"):
            find_release_version_sources(core, _hr({"name": "cm"}))


class TestRemoveReleaseVersion:
    _CM = ValuesSource("ConfigMap", "cm", "org-acme", "values")
    _SECRET = ValuesSource("Secret", "s", "org-acme", "values")

    def _patched(self, core, kind="ConfigMap"):
        patch = core.patch_namespaced_secret if kind == "Secret" else core.patch_namespaced_config_map
        body = patch.call_args.kwargs["body"]["data"]["values"]
        return yaml.safe_load(base64.b64decode(body).decode() if kind == "Secret" else body)

    def test_removes_value_and_keeps_siblings(self):
        core = _core(configmaps={"cm": {"values": _WITH_VERSION}})
        remove_release_version(core, self._CM)
        assert self._patched(core) == {"global": {"metadata": {"name": "mycluster"}}}
        assert core.patch_namespaced_config_map.call_args.kwargs["name"] == "cm"
        assert core.patch_namespaced_config_map.call_args.kwargs["namespace"] == "org-acme"

    def test_keeps_other_release_fields(self):
        core = _core(configmaps={"cm": {"values": "global:\n  release:\n    version: 1\n    x: y\n"}})
        remove_release_version(core, self._CM)
        assert self._patched(core) == {"global": {"release": {"x": "y"}}}

    def test_prunes_empty_global(self):
        core = _core(configmaps={"cm": {"values": "global:\n  release:\n    version: 1\nfoo: bar\n"}})
        remove_release_version(core, self._CM)
        assert self._patched(core) == {"foo": "bar"}

    def test_value_that_was_everything_leaves_empty_document(self):
        core = _core(configmaps={"cm": {"values": "global:\n  release:\n    version: 1\n"}})
        remove_release_version(core, self._CM)
        assert core.patch_namespaced_config_map.call_args.kwargs["body"] == {"data": {"values": ""}}

    def test_secret_is_re_encoded(self):
        core = _core(secrets={"s": {"values": _b64(_WITH_VERSION)}})
        remove_release_version(core, self._SECRET)
        assert self._patched(core, kind="Secret") == {"global": {"metadata": {"name": "mycluster"}}}

    def test_nothing_to_remove_is_no_op(self):
        core = _core(configmaps={"cm": {"values": _WITHOUT_VERSION}})
        remove_release_version(core, self._CM)
        core.patch_namespaced_config_map.assert_not_called()

    def test_missing_resource_is_no_op(self):
        core = _core()
        remove_release_version(core, self._CM)
        core.patch_namespaced_config_map.assert_not_called()

    def test_patch_error_is_migrator_error(self):
        core = _core(configmaps={"cm": {"values": _WITH_VERSION}})
        core.patch_namespaced_config_map.side_effect = ApiException(status=409, reason="Conflict")
        with pytest.raises(MigratorError, match="failed to remove global.release.version from ConfigMap"):
            remove_release_version(core, self._CM)


def test_values_source_str():
    assert str(ValuesSource("ConfigMap", "cm", "ns", "values")) == "ConfigMap ns/cm (key values)"
