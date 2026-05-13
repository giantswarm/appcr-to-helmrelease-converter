import yaml
from click.testing import CliRunner

from main import cli


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
