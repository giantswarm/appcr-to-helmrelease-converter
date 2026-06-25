from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from fetcher import FetchError, FetchResult
from migrator import MigratorError
from main import cli


def _fetch_result(app=None, catalog=None, referenced_configs=None, dependency_helm_releases=None):
    return FetchResult(
        app=app or _APP_DICT,
        catalog=catalog or _CATALOG_DICT,
        referenced_configs=referenced_configs or {},
        dependency_helm_releases=dependency_helm_releases or {},
    )


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


_APP_DICT_FLUX = {
    **_APP_DICT,
    "metadata": {
        **_APP_DICT["metadata"],
        "labels": {
            "kustomize.toolkit.fluxcd.io/name": "my-app",
            "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
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




class TestMigrateCommand:
    def _run(self, args=None, input_text=None):
        runner = CliRunner()
        return runner.invoke(cli, ["migrate"] + (args or []), input=input_text)

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def test_fetch_error_exits_nonzero(self):
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert result.exit_code != 0

    def test_fetch_error_message_printed(self):
        with patch("fetcher.fetch", side_effect=FetchError("App giantswarm/my-app not found")):
            result = self._run(self._args())
        assert "❌" in result.output

    def test_preflight_error_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_WITH_KUBECONFIG_MISMATCH)):
            result = self._run(self._args())
        assert result.exit_code != 0

    def test_preflight_warning_continues(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_WITH_NAMESPACE_CONFIG)):
            result = self._run(self._args(), input_text="n\n")
        assert "⚠️" in result.output

    def test_flux_yaml_printed_to_stdout(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "---" in result.output

    def test_flux_yaml_contains_helm_release(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "kind: HelmRelease" in result.output

    def test_user_declines_exits_zero(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert result.exit_code == 0

    def _mock_api(self):
        return MagicMock()

    def test_confirms_exits_zero(self):
        mock_api = self._mock_api()
        mock_api.get_namespaced_custom_object.return_value = {
            "status": {"conditions": [{"type": "Ready", "status": "True", "reason": "InstallSucceeded"}]},
        }
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"):
            result = self._run(self._args(), input_text="y\nN\n")  # y=migrate, N=skip cleanup
        assert result.exit_code == 0

    def test_suspend_section_shown(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._mock_api()):
            result = self._run(self._args(), input_text="y\n")
        assert "Suspend giantswarm/my-app" in result.output

    def test_step_description_shown_with_checkmark_on_success(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._mock_api()):
            result = self._run(self._args(), input_text="y\n")
        assert "✅" in result.output
        assert "app-operator.giantswarm.io/paused" in result.output

    def test_skipped_flux_step_shows_info_message(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._mock_api()):
            result = self._run(self._args(), input_text="y\n")
        assert "kustomize.toolkit.fluxcd.io/name" in result.output
        assert "not detected" in result.output

    def test_load_client_error_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", side_effect=MigratorError("bad context")):
            result = self._run(self._args(), input_text="y\n")
        assert result.exit_code != 0

    def test_load_client_error_message_printed(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", side_effect=MigratorError("bad context")):
            result = self._run(self._args(), input_text="y\n")
        assert "bad context" in result.output

    def test_step_failure_exits_nonzero(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\ny\n")
        assert result.exit_code != 0

    def test_step_failure_message_printed(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\ny\n")
        assert "failed to patch App" in result.output

    def test_step_failure_asks_to_revert(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\ny\n")
        assert "Revert changes?" in result.output

    def test_step_failure_reverts_when_confirmed(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\ny\n")
        assert "Reverting" in result.output

    def test_step_failure_skips_revert_when_declined(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\nn\n")
        assert "Skipping revert" in result.output

    def test_revert_failure_message_printed(self):
        api = MagicMock()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api), \
             patch("migrator.MigrationRunner.revert_all", side_effect=MigratorError("revert boom")):
            result = self._run(self._args(), input_text="y\ny\n")
        assert "revert failed" in result.output

    def test_context_forwarded_to_fetcher(self):
        with patch("fetcher.fetch", return_value=_fetch_result()) as mock_fetch:
            self._run(self._args() + ["--context", "my-context"], input_text="n\n")
        mock_fetch.assert_called_once_with("my-app", "giantswarm", "my-context")

    def test_no_context_passes_none_to_fetcher(self):
        with patch("fetcher.fetch", return_value=_fetch_result()) as mock_fetch:
            self._run(self._args(), input_text="n\n")
        mock_fetch.assert_called_once_with("my-app", "giantswarm", None)

    def test_section_headers_appear_in_output(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "Fetch" in result.output
        assert "Preflight" in result.output
        assert "Resolve" in result.output
        assert "Generated Flux resources" in result.output
        assert "Confirm" in result.output

    def test_resolve_section_shows_auto_resolved_key(self):
        refs = {("ConfigMap", "my-cm", "giantswarm"): {"data": {"configmap-values.yaml": "x"}}}
        app = {**_APP_DICT, "spec": {**_APP_DICT["spec"],
               "config": {"configMap": {"name": "my-cm", "namespace": "giantswarm"}}}}
        with patch("fetcher.fetch", return_value=_fetch_result(app=app, referenced_configs=refs)):
            result = self._run(self._args(), input_text="n\n")
        assert "my-cm" in result.output
        assert "configmap-values.yaml" in result.output

    def test_fetch_step_shows_app_name_and_version(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "my-app" in result.output
        assert "1.2.3" in result.output

    def test_fetch_step_displays_app_cr_yaml(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "kind: App" in result.output

    def test_fetch_step_strips_server_fields_from_app_cr(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_DICT_WITH_SERVER_FIELDS)):
            result = self._run(self._args(), input_text="n\n")
        assert "abc-123" not in result.output
        assert "resourceVersion" not in result.output

    def test_suspend_chart_cr_step_shown_on_success(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._mock_api()):
            result = self._run(self._args(), input_text="y\n")
        assert "chart-operator.giantswarm.io/paused" in result.output

    def test_in_cluster_app_does_not_call_load_wc_client(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._mock_api()), \
             patch("migrator.load_wc_client") as mock_load_wc:
            self._run(self._args(), input_text="y\n")
        mock_load_wc.assert_not_called()

    def _remote_app(self):
        return {
            **_APP_DICT,
            "spec": {
                **_APP_DICT["spec"],
                "kubeConfig": {
                    "inCluster": False,
                    "secret": {"name": "my-cluster-kubeconfig", "namespace": "giantswarm"},
                },
            },
        }

    def test_remote_cluster_app_calls_load_wc_client(self):
        mock_wc_api = MagicMock()
        with patch("fetcher.fetch", return_value=_fetch_result(app=self._remote_app())), \
             patch("migrator.load_client", return_value=self._mock_api()), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", return_value=mock_wc_api) as mock_load_wc:
            self._run(self._args(), input_text="y\n")
        mock_load_wc.assert_called_once()

    def test_load_wc_client_error_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=self._remote_app())), \
             patch("migrator.load_client", return_value=self._mock_api()), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", side_effect=MigratorError("bad kubeconfig secret")):
            result = self._run(self._args(), input_text="y\n")
        assert result.exit_code != 0

    def test_load_wc_client_error_message_printed(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=self._remote_app())), \
             patch("migrator.load_client", return_value=self._mock_api()), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", side_effect=MigratorError("bad kubeconfig secret")):
            result = self._run(self._args(), input_text="y\n")
        assert "bad kubeconfig secret" in result.output

    def test_fetch_step_shows_catalog_type(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "oci" in result.output

    def test_no_issues_message_when_preflight_clean(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args(), input_text="n\n")
        assert "No issues found" in result.output

    def test_preflight_summary_shown_when_warnings(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_WITH_NAMESPACE_CONFIG)):
            result = self._run(self._args(), input_text="n\n")
        assert "warning" in result.output.lower()

    def test_dep_helmrelease_missing_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result(dependency_helm_releases={"coredns": None})):
            result = self._run(self._args())
        assert result.exit_code != 0

    def test_dep_helmrelease_missing_shows_error(self):
        with patch("fetcher.fetch", return_value=_fetch_result(dependency_helm_releases={"coredns": None})):
            result = self._run(self._args())
        assert "coredns" in result.output

    def test_dep_info_note_shown_when_deps_present(self):
        hr = {"metadata": {"name": "coredns"}}
        with patch("fetcher.fetch", return_value=_fetch_result(dependency_helm_releases={"coredns": hr})):
            result = self._run(self._args(), input_text="n\n")
        assert "existence only" in result.output
        assert "giantswarm/coredns" in result.output

    def _apply_failing_api(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {}  # SuspendChart GET
        return api

    def test_apply_step_failure_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._apply_failing_api()), \
             patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn_cls.return_value.request.side_effect = ApiException(status=500)
            result = self._run(self._args(), input_text="y\ny\n")
        assert result.exit_code != 0

    def test_apply_step_failure_asks_to_revert(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._apply_failing_api()), \
             patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn_cls.return_value.request.side_effect = ApiException(status=500)
            result = self._run(self._args(), input_text="y\ny\n")
        assert "Revert changes?" in result.output

    def test_apply_step_failure_skips_revert_when_declined(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._apply_failing_api()), \
             patch("migrator.apply_flux_resources.DynamicClient") as mock_dyn_cls:
            mock_dyn_cls.return_value.request.side_effect = ApiException(status=500)
            result = self._run(self._args(), input_text="y\nn\n")
        assert "Skipping revert" in result.output

    def test_dry_run_exits_zero_without_input(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run"])
        assert result.exit_code == 0

    def test_dry_run_does_not_show_confirm_section(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run"])
        assert "Confirm" not in result.output

    def test_dry_run_shows_halt_message(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run"])
        assert "✅ Dry run complete" in result.output

    def test_dry_run_does_not_call_load_client(self):
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client") as mock_load:
            self._run(self._args() + ["--dry-run"])
        mock_load.assert_not_called()

    def test_dry_run_still_displays_generated_flux_resources(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run"])
        assert "kind: HelmRelease" in result.output

    def test_dry_run_preflight_error_still_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_WITH_KUBECONFIG_MISMATCH)):
            result = self._run(self._args() + ["--dry-run"])
        assert result.exit_code != 0

    def test_output_file_contains_helm_release(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("fetcher.fetch", return_value=_fetch_result()):
                runner.invoke(cli, ["migrate"] + self._args() + ["--output", "out.yaml"], input="n\n")
            with open("out.yaml") as f:
                docs = list(yaml.safe_load_all(f))
        assert any(d.get("kind") == "HelmRelease" for d in docs if d)

    def test_output_file_save_message_shown(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("fetcher.fetch", return_value=_fetch_result()):
                result = runner.invoke(cli, ["migrate"] + self._args() + ["--output", "out.yaml"], input="n\n")
        assert "✅ Conversion result saved to out.yaml!" in result.output

    def test_dry_run_with_output_writes_file(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("fetcher.fetch", return_value=_fetch_result()):
                result = runner.invoke(cli, ["migrate"] + self._args() + ["--dry-run", "--output", "out.yaml"])
            assert result.exit_code == 0
            with open("out.yaml") as f:
                docs = list(yaml.safe_load_all(f))
        assert any(d.get("kind") == "HelmRelease" for d in docs if d)

    def test_output_bad_path_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--output", "/"])
        assert result.exit_code != 0

    def _successful_migration_mocks(self, app=None):
        mock_api = MagicMock()
        mock_api.get_namespaced_custom_object.return_value = {
            "metadata": {"annotations": {}, "finalizers": []},
            "status": {"conditions": [{"type": "Ready", "status": "True", "reason": "InstallSucceeded"}]},
        }
        fetch_result = _fetch_result(app=app or _APP_DICT)
        return mock_api, fetch_result

    def test_cleanup_section_shown_after_successful_migration(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"):
            result = self._run(self._args(), input_text="y\n")
        assert "Clean-up" in result.output

    def test_flux_managed_app_prints_cleanup_instructions(self):
        mock_api, fetch_result = self._successful_migration_mocks(app=_APP_DICT_FLUX)
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"):
            result = self._run(self._args(), input_text="y\n")
        assert "operatorkit.giantswarm.io/app-operator-app" in result.output
        assert "operatorkit.giantswarm.io/chart-operator-chart" in result.output

    def test_non_flux_cleanup_confirmed_deletes_and_shows_success(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args(), input_text="y\ny\n")
        mock_delete.assert_called_once()
        assert "✅ App CR and Chart CR deleted." in result.output

    def test_non_flux_cleanup_failure_exits_nonzero(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart", side_effect=MigratorError("delete failed")):
            result = self._run(self._args(), input_text="y\ny\n")
        assert result.exit_code != 0
        assert "delete failed" in result.output


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
