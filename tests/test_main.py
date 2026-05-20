from unittest.mock import patch

import yaml
from click.testing import CliRunner

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


APP_WITH_NAMESPACE_CONFIG_YAML = """\
apiVersion: application.giantswarm.io/v1alpha1
kind: App
metadata:
  name: my-app
  namespace: giantswarm
spec:
  name: my-app
  namespace: monitoring
  version: 1.2.3
  namespaceConfig:
    annotations:
      linkerd.io/inject: enabled
    labels:
      some-label: some-value
"""

APP_WITH_KUBECONFIG_NAMESPACE_MISMATCH_YAML = """\
apiVersion: application.giantswarm.io/v1alpha1
kind: App
metadata:
  name: my-app
  namespace: org-giantswarm
spec:
  name: my-app
  namespace: monitoring
  version: 1.2.3
  kubeConfig:
    inCluster: false
    secret:
      name: example-kubeconfig
      namespace: other-ns
"""

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


class TestConvertCommand:
    def _run(self, input_text=None, args=None):
        runner = CliRunner()
        return runner.invoke(cli, ["convert"] + (args or []), input=input_text)

    def test_output_starts_with_separator(self):
        result = self._run(input_text=MINIMAL_APP_YAML)
        assert result.output.startswith("---\n")

    def test_output_contains_two_yaml_documents(self):
        result = self._run(input_text=MINIMAL_APP_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert len(docs) == 2

    def test_output_oci_repository_first(self):
        result = self._run(input_text=MINIMAL_APP_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert docs[0]["kind"] == "OCIRepository"

    def test_output_helm_release_second(self):
        result = self._run(input_text=MINIMAL_APP_YAML)
        docs = list(yaml.safe_load_all(result.output))
        assert docs[1]["kind"] == "HelmRelease"

    def test_exit_code_zero(self):
        result = self._run(input_text=MINIMAL_APP_YAML)
        assert result.exit_code == 0

    def test_preflight_warning_printed_to_stderr(self):
        result = self._run(input_text=APP_WITH_NAMESPACE_CONFIG_YAML)
        assert "warning:" in result.output

    def test_preflight_warning_exits_zero(self):
        result = self._run(input_text=APP_WITH_NAMESPACE_CONFIG_YAML)
        assert result.exit_code == 0

    def test_preflight_error_printed_to_stderr(self):
        result = self._run(input_text=APP_WITH_KUBECONFIG_NAMESPACE_MISMATCH_YAML)
        assert "error:" in result.output

    def test_preflight_error_exits_nonzero(self):
        result = self._run(input_text=APP_WITH_KUBECONFIG_NAMESPACE_MISMATCH_YAML)
        assert result.exit_code != 0


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
