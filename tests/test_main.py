from unittest.mock import patch

import yaml
from click.testing import CliRunner

from fetcher import FetchError
from main import cli


_APP_DICT = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "App",
    "metadata": {"name": "my-app", "namespace": "giantswarm"},
    "spec": {
        "name": "my-app",
        "namespace": "monitoring",
        "version": "1.2.3",
        "catalog": "giantswarm",
        "catalogNamespace": "giantswarm",
    },
}

_CATALOG_DICT = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "Catalog",
    "metadata": {"name": "giantswarm", "namespace": "giantswarm"},
    "spec": {
        "repositories": [{"type": "oci", "URL": "oci://gsoci.azurecr.io/charts/giantswarm"}],
    },
}

_APP_DICT_WITH_SERVER_FIELDS = {
    **_APP_DICT,
    "metadata": {
        "name": "my-app",
        "namespace": "giantswarm",
        "uid": "abc-123",
        "resourceVersion": "99999",
        "generation": 3,
        "creationTimestamp": "2024-01-01T00:00:00Z",
        "selfLink": "/apis/application.giantswarm.io/v1alpha1/namespaces/giantswarm/apps/my-app",
        "managedFields": [{"manager": "app-operator", "operation": "Apply"}],
        "labels": {"app": "my-app"},
        "annotations": {
            "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"..."}',
            "some-annotation": "keep-me",
        },
    },
}


_APP_WITH_NAMESPACE_CONFIG = {
    **_APP_DICT,
    "spec": {**_APP_DICT["spec"], "namespaceConfig": {"annotations": {"linkerd.io/inject": "enabled"}}},
}

_APP_WITH_KUBECONFIG_MISMATCH = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "App",
    "metadata": {"name": "my-app", "namespace": "org-giantswarm"},
    "spec": {
        "name": "my-app",
        "namespace": "monitoring",
        "version": "1.2.3",
        "catalog": "giantswarm",
        "kubeConfig": {"inCluster": False, "secret": {"name": "example-kubeconfig", "namespace": "other-ns"}},
    },
}

_CATALOG_MULTI_HELM = {
    "apiVersion": "application.giantswarm.io/v1alpha1",
    "kind": "Catalog",
    "metadata": {"name": "example", "namespace": "giantswarm"},
    "spec": {"repositories": [
        {"type": "helm", "URL": "https://charts.example.io"},
        {"type": "helm", "URL": "https://mirror.example.io"},
    ]},
}

_NAMESPACE_CONFIG_MULTI_DOC = "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_APP_WITH_NAMESPACE_CONFIG)
_KUBECONFIG_MISMATCH_MULTI_DOC = "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_APP_WITH_KUBECONFIG_MISMATCH)
_MULTI_HELM_MULTI_DOC = "---\n" + yaml.dump(_CATALOG_MULTI_HELM) + "---\n" + yaml.dump(_APP_DICT)

_MULTI_DOC_YAML = "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_APP_DICT)


class TestConvertCommand:
    def _run(self, input_text=None, args=None):
        runner = CliRunner()
        return runner.invoke(cli, ["convert"] + (args or []), input=input_text)

    def test_multi_doc_input_succeeds(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        assert result.exit_code == 0

    def test_output_starts_with_separator(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        assert result.output.startswith("---\n")

    def test_output_contains_two_yaml_documents(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert len(docs) == 2

    def test_output_oci_repository_first(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert docs[0]["kind"] == "OCIRepository"

    def test_output_helm_release_second(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert docs[1]["kind"] == "HelmRelease"

    def test_exit_code_zero(self):
        result = self._run(input_text=_MULTI_DOC_YAML)
        assert result.exit_code == 0

    def test_preflight_warning_printed_to_stderr(self):
        result = self._run(input_text=_NAMESPACE_CONFIG_MULTI_DOC)
        assert "⚠️" in result.output

    def test_preflight_warning_exits_zero(self):
        result = self._run(input_text=_NAMESPACE_CONFIG_MULTI_DOC)
        assert result.exit_code == 0

    def test_preflight_error_printed_to_stderr(self):
        result = self._run(input_text=_KUBECONFIG_MISMATCH_MULTI_DOC)
        assert "❌" in result.output

    def test_preflight_error_exits_nonzero(self):
        result = self._run(input_text=_KUBECONFIG_MISMATCH_MULTI_DOC)
        assert result.exit_code != 0

    def test_missing_catalog_cr_exits_nonzero(self):
        only_app = yaml.dump(_APP_DICT)
        result = self._run(input_text=only_app)
        assert result.exit_code != 0

    def test_missing_catalog_cr_prints_error(self):
        only_app = yaml.dump(_APP_DICT)
        result = self._run(input_text=only_app)
        assert "error:" in result.output

    def test_missing_app_cr_exits_nonzero(self):
        only_catalog = yaml.dump(_CATALOG_DICT)
        result = self._run(input_text=only_catalog)
        assert result.exit_code != 0

    def test_missing_app_cr_prints_error(self):
        only_catalog = yaml.dump(_CATALOG_DICT)
        result = self._run(input_text=only_catalog)
        assert "error:" in result.output

    def test_duplicate_app_cr_exits_nonzero(self):
        two_apps = "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_APP_DICT) + "---\n" + yaml.dump(_APP_DICT)
        result = self._run(input_text=two_apps)
        assert result.exit_code != 0

    def test_duplicate_catalog_cr_exits_nonzero(self):
        two_catalogs = "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_CATALOG_DICT) + "---\n" + yaml.dump(_APP_DICT)
        result = self._run(input_text=two_catalogs)
        assert result.exit_code != 0

    def test_non_gs_docs_alongside_required_crs_are_ignored(self):
        extra = {"apiVersion": "other.io/v1", "kind": "Something", "metadata": {"name": "x"}}
        with_extra = "---\n" + yaml.dump(extra) + "---\n" + _MULTI_DOC_YAML
        result = self._run(input_text=with_extra)
        assert result.exit_code == 0

    def test_empty_separator_docs_are_ignored(self):
        with_empty = "---\n---\n" + _MULTI_DOC_YAML
        result = self._run(input_text=with_empty)
        assert result.exit_code == 0

    def test_gs_versioned_unknown_kind_is_ignored(self):
        unknown = {"apiVersion": "application.giantswarm.io/v1alpha1", "kind": "Chart", "metadata": {"name": "x"}}
        with_unknown = "---\n" + yaml.dump(unknown) + "---\n" + _MULTI_DOC_YAML
        result = self._run(input_text=with_unknown)
        assert result.exit_code == 0

    def test_multiple_helm_repos_emits_warning(self):
        result = self._run(input_text=_MULTI_HELM_MULTI_DOC)
        assert "⚠️" in result.output

    def test_multiple_helm_repos_exits_zero(self):
        result = self._run(input_text=_MULTI_HELM_MULTI_DOC)
        assert result.exit_code == 0

    def test_single_helm_repo_no_warning(self):
        single_helm = {
            "apiVersion": "application.giantswarm.io/v1alpha1",
            "kind": "Catalog",
            "metadata": {"name": "example", "namespace": "giantswarm"},
            "spec": {"repositories": [{"type": "helm", "URL": "https://charts.example.io"}]},
        }
        multi_doc = "---\n" + yaml.dump(single_helm) + "---\n" + yaml.dump(_APP_DICT)
        result = self._run(input_text=multi_doc)
        assert "⚠️" not in result.output


class TestFetchCommand:
    def _run(self, args=None):
        runner = CliRunner()
        return runner.invoke(cli, ["fetch"] + (args or []))

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def test_output_starts_with_separator(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.output.startswith("---\n")

    def test_catalog_cr_emitted_first(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        assert docs[0]["kind"] == "Catalog"

    def test_app_cr_emitted_second(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        assert docs[1]["kind"] == "App"

    def test_exit_code_zero_on_success(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.exit_code == 0

    def test_fetch_error_message_printed(self):
        from fetcher import FetchError
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert "error:" in result.output

    def test_fetch_error_exits_nonzero(self):
        from fetcher import FetchError
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert result.exit_code != 0

    def test_context_forwarded_to_fetcher(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)) as mock_fetch:
            self._run(self._args() + ["--context", "my-context"])
        mock_fetch.assert_called_once_with("my-app", "giantswarm", "my-context")

    def test_no_context_passes_none_to_fetcher(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)) as mock_fetch:
            self._run(self._args())
        mock_fetch.assert_called_once_with("my-app", "giantswarm", None)

    def test_server_side_metadata_fields_stripped_from_output(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT_WITH_SERVER_FIELDS, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        app_meta = docs[1]["metadata"]
        for field in ("uid", "resourceVersion", "generation", "creationTimestamp",
                      "selfLink", "managedFields"):
            assert field not in app_meta, f"{field} should be stripped"

    def test_server_annotation_stripped_from_output(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT_WITH_SERVER_FIELDS, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        app_meta = docs[1]["metadata"]
        assert "kubectl.kubernetes.io/last-applied-configuration" not in app_meta.get("annotations", {})

    def test_non_server_annotations_preserved_in_output(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT_WITH_SERVER_FIELDS, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        app_meta = docs[1]["metadata"]
        assert app_meta.get("annotations", {}).get("some-annotation") == "keep-me"

    def test_non_server_labels_preserved_in_output(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT_WITH_SERVER_FIELDS, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        app_meta = docs[1]["metadata"]
        assert app_meta.get("labels", {}).get("app") == "my-app"


class TestFetchAndConvertCommand:
    def _run(self, args=None):
        runner = CliRunner()
        return runner.invoke(cli, ["fetch-and-convert"] + (args or []))

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def test_exit_code_zero_on_success(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.exit_code == 0

    def test_output_starts_with_separator(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.output.startswith("---\n")

    def test_output_oci_repository_first(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        assert docs[0]["kind"] == "OCIRepository"

    def test_output_helm_release_second(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)):
            result = self._run(self._args())
        docs = list(yaml.safe_load_all(result.output))
        assert docs[1]["kind"] == "HelmRelease"

    def test_fetch_error_exits_nonzero(self):
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert result.exit_code != 0

    def test_fetch_error_message_printed(self):
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert "error:" in result.output

    def test_context_forwarded_to_fetcher(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)) as mock_fetch:
            self._run(self._args() + ["--context", "my-context"])
        mock_fetch.assert_called_once_with("my-app", "giantswarm", "my-context")

    def test_no_context_passes_none_to_fetcher(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_DICT)) as mock_fetch:
            self._run(self._args())
        mock_fetch.assert_called_once_with("my-app", "giantswarm", None)

    def test_multiple_helm_repos_emits_warning(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_MULTI_HELM)):
            result = self._run(self._args())
        assert "⚠️" in result.output

    def test_multiple_helm_repos_exits_zero(self):
        with patch("fetcher.fetch", return_value=(_APP_DICT, _CATALOG_MULTI_HELM)):
            result = self._run(self._args())
        assert result.exit_code == 0

    def test_preflight_warning_printed(self):
        with patch("fetcher.fetch", return_value=(_APP_WITH_NAMESPACE_CONFIG, _CATALOG_DICT)):
            result = self._run(self._args())
        assert "⚠️" in result.output

    def test_preflight_warning_exits_zero(self):
        with patch("fetcher.fetch", return_value=(_APP_WITH_NAMESPACE_CONFIG, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.exit_code == 0

    def test_preflight_error_printed(self):
        with patch("fetcher.fetch", return_value=(_APP_WITH_KUBECONFIG_MISMATCH, _CATALOG_DICT)):
            result = self._run(self._args())
        assert "❌" in result.output

    def test_preflight_error_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=(_APP_WITH_KUBECONFIG_MISMATCH, _CATALOG_DICT)):
            result = self._run(self._args())
        assert result.exit_code != 0


class TestStripServerFields:
    def setup_method(self):
        from main import _strip_server_fields
        self.strip = _strip_server_fields

    def test_doc_without_metadata_returned_unchanged(self):
        doc = {"kind": "Unknown", "spec": {}}
        assert self.strip(doc) is doc

    def test_annotations_key_removed_when_only_server_annotation_present(self):
        doc = {
            "kind": "App",
            "metadata": {
                "name": "x",
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": "...",
                },
            },
        }
        result = self.strip(doc)
        assert "annotations" not in result["metadata"]
