from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner
from kubernetes.client.exceptions import ApiException

from fetcher import FetchError, FetchResult
from migrator import MigratorError
from main import cli


def _fetch_result(app=None, catalog=None, referenced_configs=None, dependency_helm_releases=None,
                  pull_secret=None):
    return FetchResult(
        app=app or _APP_DICT,
        catalog=catalog or _CATALOG_DICT,
        referenced_configs=referenced_configs or {},
        dependency_helm_releases=dependency_helm_releases or {},
        pull_secret=pull_secret,
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

    def test_missing_chart_cr_hard_fails_instead_of_proceeding_to_apply(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=api), \
             patch("migrator.apply_flux_resources.DynamicClient") as mock_dynamic:
            result = self._run(self._args(), input_text="y\nn\n")
        assert result.exit_code != 0
        assert "failed to fetch Chart" in result.output
        mock_dynamic.assert_not_called()

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

    def test_revert_notes_printed_when_revert_declined(self):
        already_paused_app = {
            **_APP_DICT,
            "metadata": {
                **_APP_DICT["metadata"],
                "annotations": {"app-operator.giantswarm.io/paused": "true"},
            },
        }
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {}
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)
        with patch("fetcher.fetch", return_value=_fetch_result(app=already_paused_app)), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\nn\n")
        assert "was already paused" in result.output
        assert "kubectl annotate app" in result.output

    def test_revert_notes_printed_after_revert(self):
        # App already paused: SuspendApp skips (no patch), SuspendChart fails → revert prints note
        already_paused_app = {
            **_APP_DICT,
            "metadata": {
                **_APP_DICT["metadata"],
                "annotations": {"app-operator.giantswarm.io/paused": "true"},
            },
        }
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {}  # SuspendChart GET succeeds
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403)  # SuspendChart PATCH fails
        with patch("fetcher.fetch", return_value=_fetch_result(app=already_paused_app)), \
             patch("migrator.load_client", return_value=api):
            result = self._run(self._args(), input_text="y\ny\n")
        assert "was already paused" in result.output
        assert "kubectl annotate app" in result.output

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
        mock_fetch.assert_called_once_with("my-app", "giantswarm", "my-context", None)

    def test_no_context_passes_none_to_fetcher(self):
        with patch("fetcher.fetch", return_value=_fetch_result()) as mock_fetch:
            self._run(self._args(), input_text="n\n")
        mock_fetch.assert_called_once_with("my-app", "giantswarm", None, None)

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
        assert "python main.py cleanup --name my-app --namespace giantswarm" in result.output
        assert "operatorkit.giantswarm.io/app-operator-app" in result.output
        assert "operatorkit.giantswarm.io/chart-operator-chart" in result.output

    def test_non_flux_cleanup_confirmed_deletes_and_shows_success(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart", return_value=[]) as mock_delete:
            result = self._run(self._args(), input_text="y\ny\n")
        mock_delete.assert_called_once_with(mock_api, mock_api, fetch_result.app)
        assert "✅ App CR and Chart CR deleted." in result.output

    def test_non_flux_cleanup_prints_notes_from_delete(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        note = "Chart giantswarm/my-app not found — skipped, nothing to delete."
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart", return_value=[note]):
            result = self._run(self._args(), input_text="y\ny\n")
        assert note in result.output
        assert "✅ App CR and Chart CR deleted." not in result.output
        assert "✅ App CR deleted." in result.output

    def test_non_flux_cleanup_failure_exits_nonzero(self):
        mock_api, fetch_result = self._successful_migration_mocks()
        with patch("fetcher.fetch", return_value=fetch_result), \
             patch("migrator.load_client", return_value=mock_api), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart", side_effect=MigratorError("delete failed"), return_value=[]):
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


_APP_WITH_MULTIKEY_CONFIG = {
    **_APP_DICT,
    "spec": {**_APP_DICT["spec"],
             "config": {"configMap": {"name": "my-cm", "namespace": "giantswarm"}}},
}

_MULTIKEY_REFS = {("ConfigMap", "my-cm", "giantswarm"): {"data": {"values.yaml": "x", "alt.yaml": "y"}}}


class TestNonInteractiveFlags:
    def _run(self, args=None, input_text=None):
        return CliRunner().invoke(cli, ["migrate"] + (args or []), input=input_text)

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def _ready_api(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {
            "status": {"conditions": [{"type": "Ready", "status": "True", "reason": "InstallSucceeded"}]},
        }
        return api

    def test_malformed_values_key_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run", "--values-key", "garbage"])
        assert result.exit_code != 0

    def test_malformed_values_key_shows_format_hint(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run", "--values-key", "garbage"])
        assert "KIND/NAME=KEY" in result.output

    def test_values_key_pre_answers_prompt_in_dry_run(self):
        with patch("fetcher.fetch",
                   return_value=_fetch_result(app=_APP_WITH_MULTIKEY_CONFIG, referenced_configs=_MULTIKEY_REFS)), \
             patch("click.prompt") as mock_prompt:
            result = self._run(self._args() + ["--dry-run", "--values-key", "ConfigMap/my-cm=alt.yaml"])
        mock_prompt.assert_not_called()
        assert result.exit_code == 0
        assert "(--values-key)" in result.output

    def test_assume_yes_without_values_key_errors_on_multikey(self):
        with patch("fetcher.fetch",
                   return_value=_fetch_result(app=_APP_WITH_MULTIKEY_CONFIG, referenced_configs=_MULTIKEY_REFS)):
            result = self._run(self._args() + ["--dry-run", "--assume-yes"])
        assert result.exit_code != 0
        assert "multiple keys" in result.output

    def test_assume_yes_skips_proceed_prompt(self):
        with patch("fetcher.fetch", return_value=_fetch_result(app=_APP_DICT_FLUX)), \
             patch("migrator.load_client", return_value=self._ready_api()), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"):
            result = self._run(self._args() + ["--assume-yes"])  # no input supplied
        assert result.exit_code == 0
        assert "--assume-yes" in result.output

    def test_assume_yes_does_not_auto_confirm_cr_deletion(self):
        # Non-Flux app reaches the destructive delete prompt; --assume-yes must NOT auto-confirm it.
        with patch("fetcher.fetch", return_value=_fetch_result()), \
             patch("migrator.load_client", return_value=self._ready_api()), \
             patch("migrator.apply_flux_resources.DynamicClient"), \
             patch("migrator.monitor_helm_release.time.sleep"), \
             patch("main.delete_app_and_chart", return_value=[]) as mock_delete:
            self._run(self._args() + ["--assume-yes"], input_text="n\n")
        mock_delete.assert_not_called()


class TestCleanupCommand:
    def _run(self, args=None, input_text=None):
        return CliRunner().invoke(cli, ["cleanup"] + (args or []), input=input_text)

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def _mock_api(self, app=None):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = app or _APP_DICT
        return api

    def test_app_not_found_exits_zero(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0

    def test_app_not_found_prints_info_and_skips_delete(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("migrator.load_client", return_value=api), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args())
        assert "ℹ️" in result.output
        mock_delete.assert_not_called()

    def test_app_fetch_other_error_exits_nonzero(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=500, reason="boom")
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "❌" in result.output
        assert "boom" in result.output

    def test_load_client_failure_exits_nonzero(self):
        with patch("migrator.load_client", side_effect=MigratorError("bad context")):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "bad context" in result.output

    def test_verification_failure_exits_nonzero_and_prints_all_failures(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=["failure one", "failure two"]), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "failure one" in result.output
        assert "failure two" in result.output
        mock_delete.assert_not_called()

    def test_verification_success_prints_checkmark(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[]):
            result = self._run(self._args() + ["--dry-run"])
        assert "✅" in result.output

    def test_dry_run_exits_zero_without_deleting(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args() + ["--dry-run"])
        assert result.exit_code == 0
        assert "Dry run complete" in result.output
        mock_delete.assert_not_called()

    def test_dry_run_shows_what_will_be_deleted(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]):
            result = self._run(self._args() + ["--dry-run"])
        assert "Chart giantswarm/my-app" in result.output
        assert "App giantswarm/my-app" in result.output

    def test_flux_managed_app_shows_gitops_warning(self):
        api = self._mock_api(app=_APP_DICT_FLUX)
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]):
            result = self._run(self._args() + ["--dry-run"])
        assert "gitops" in result.output.lower()

    def test_non_flux_app_shows_no_gitops_warning(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]):
            result = self._run(self._args() + ["--dry-run"])
        assert "gitops" not in result.output.lower()

    def test_prompt_declined_exits_zero_without_deleting(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args(), input_text="n\n")
        assert result.exit_code == 0
        mock_delete.assert_not_called()

    def test_prompt_confirmed_deletes(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[]) as mock_delete:
            result = self._run(self._args(), input_text="y\n")
        assert result.exit_code == 0
        mock_delete.assert_called_once_with(api, api, _APP_DICT)

    def test_assume_yes_deletes_without_prompting(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[]) as mock_delete:
            result = self._run(self._args() + ["--assume-yes"])  # no input supplied
        assert result.exit_code == 0
        mock_delete.assert_called_once()

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
        api = self._mock_api(app=self._remote_app())
        mock_wc_api = MagicMock()
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", return_value=mock_wc_api) as mock_load_wc, \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[]) as mock_delete:
            result = self._run(self._args() + ["--assume-yes"])
        mock_load_wc.assert_called_once()
        mock_delete.assert_called_once_with(api, mock_wc_api, self._remote_app())
        assert result.exit_code == 0

    def test_load_wc_client_failure_exits_nonzero_before_delete(self):
        api = self._mock_api(app=self._remote_app())
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", side_effect=MigratorError("bad kubeconfig secret")), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart") as mock_delete:
            result = self._run(self._args() + ["--assume-yes"])
        assert result.exit_code != 0
        assert "bad kubeconfig secret" in result.output
        mock_delete.assert_not_called()

    def test_delete_failure_exits_nonzero(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", side_effect=MigratorError("delete failed")):
            result = self._run(self._args() + ["--assume-yes"])
        assert result.exit_code != 0
        assert "delete failed" in result.output

    def test_notes_from_delete_are_printed(self):
        api = self._mock_api()
        note = "Chart giantswarm/my-app not found — skipped, nothing to delete."
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[note]):
            result = self._run(self._args() + ["--assume-yes"])
        assert note in result.output
        assert "✅ App CR and Chart CR deleted." not in result.output
        assert "✅ App CR deleted." in result.output

    def test_happy_path_prints_success(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api), \
             patch("main.verify_migration", return_value=[]), \
             patch("main.delete_app_and_chart", return_value=[]):
            result = self._run(self._args() + ["--assume-yes"])
        assert result.exit_code == 0
        assert "✅ App CR and Chart CR deleted." in result.output


class TestPullSecretFlag:
    def _run(self, args=None, input_text=None):
        runner = CliRunner()
        return runner.invoke(cli, ["migrate"] + (args or []), input=input_text)

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    _DOCKER_SECRET = {"type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": "e30="}}

    def test_name_forwarded_to_fetcher(self):
        with patch("fetcher.fetch", return_value=_fetch_result(pull_secret=self._DOCKER_SECRET)) as mock_fetch:
            self._run(self._args() + ["--pull-secret", "regcred", "--dry-run"])
        mock_fetch.assert_called_once_with("my-app", "giantswarm", None, "regcred")

    def test_secret_ref_appears_in_generated_source(self):
        with patch("fetcher.fetch", return_value=_fetch_result(pull_secret=self._DOCKER_SECRET)):
            result = self._run(self._args() + ["--pull-secret", "regcred", "--dry-run"])
        assert "secretRef" in result.output

    def test_missing_secret_exits_nonzero(self):
        with patch("fetcher.fetch", return_value=_fetch_result(pull_secret=None)):
            result = self._run(self._args() + ["--pull-secret", "regcred", "--dry-run"])
        assert result.exit_code == 1

    def test_missing_secret_message_shown(self):
        with patch("fetcher.fetch", return_value=_fetch_result(pull_secret=None)):
            result = self._run(self._args() + ["--pull-secret", "regcred", "--dry-run"])
        assert "regcred" in result.output and "not found" in result.output

    def test_wrong_shape_warns_but_continues(self):
        wrong = {"type": "Opaque", "data": {"username": "dXNlcg=="}}
        with patch("fetcher.fetch", return_value=_fetch_result(pull_secret=wrong)):
            result = self._run(self._args() + ["--pull-secret", "regcred", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run complete" in result.output

    def test_no_flag_leaves_source_unauthenticated(self):
        with patch("fetcher.fetch", return_value=_fetch_result()):
            result = self._run(self._args() + ["--dry-run"])
        assert "secretRef" not in result.output


class TestSuspendCommand:
    def _run(self, args=None):
        return CliRunner().invoke(cli, ["suspend"] + (args or []))

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def _mock_api(self, app=None, chart_cr=None):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = [
            app or _APP_DICT,
            chart_cr if chart_cr is not None else {"metadata": {"name": "my-app", "namespace": "giantswarm"}},
        ]
        return api

    def test_app_not_found_exits_zero(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "ℹ️" in result.output

    def test_happy_path_pauses_app_and_chart_and_exits_zero(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "✅" in result.output
        annotate_calls = [
            c.kwargs["body"]["metadata"]["annotations"]
            for c in api.patch_namespaced_custom_object.call_args_list
        ]
        assert {"app-operator.giantswarm.io/paused": "true"} in annotate_calls
        assert {"chart-operator.giantswarm.io/paused": "true"} in annotate_calls

    def test_already_paused_app_shows_already_set_not_checkmark(self):
        app = {
            **_APP_DICT,
            "metadata": {**_APP_DICT["metadata"], "annotations": {"app-operator.giantswarm.io/paused": "true"}},
        }
        api = self._mock_api(app=app)
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "already set, nothing to do" in result.output

    def test_chart_not_found_shows_skip_message(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = [_APP_DICT, ApiException(status=404)]
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "Chart CR giantswarm/my-app not found — skipped" in result.output

    def test_step_failure_exits_nonzero(self):
        api = self._mock_api()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403, reason="forbidden")
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "❌" in result.output

    def test_load_client_failure_exits_nonzero(self):
        with patch("migrator.load_client", side_effect=MigratorError("bad context")):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "bad context" in result.output

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
        api = self._mock_api(app=self._remote_app())
        mock_wc_api = MagicMock()
        mock_wc_api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": "my-app", "namespace": "giantswarm"}
        }
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", return_value=mock_wc_api) as mock_load_wc:
            result = self._run(self._args())
        mock_load_wc.assert_called_once()
        assert result.exit_code == 0

    def test_load_wc_client_failure_exits_nonzero(self):
        api = self._mock_api(app=self._remote_app())
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", side_effect=MigratorError("bad kubeconfig secret")):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "bad kubeconfig secret" in result.output


class TestResumeCommand:
    def _run(self, args=None):
        return CliRunner().invoke(cli, ["resume"] + (args or []))

    def _args(self):
        return ["--name", "my-app", "--namespace", "giantswarm"]

    def _mock_api(self, app=None):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = app or _APP_DICT
        return api

    def test_happy_path_unpauses_app_and_chart_and_exits_zero(self):
        api = self._mock_api()
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "✅" in result.output
        annotate_calls = [
            c.kwargs["body"]["metadata"]["annotations"]
            for c in api.patch_namespaced_custom_object.call_args_list
        ]
        assert {"app-operator.giantswarm.io/paused": None} in annotate_calls
        assert {"chart-operator.giantswarm.io/paused": None} in annotate_calls

    def test_app_not_found_exits_zero(self):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "ℹ️" in result.output

    def test_chart_not_found_shows_skip_note_and_app_only_message(self):
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = _APP_DICT
        api.patch_namespaced_custom_object.side_effect = [None, ApiException(status=404)]
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code == 0
        assert "Chart giantswarm/my-app not found — skipped, nothing to resume." in result.output
        assert "✅ App CR unpaused." in result.output
        assert "✅ App CR and Chart CR unpaused." not in result.output

    def test_step_failure_exits_nonzero(self):
        api = self._mock_api()
        api.patch_namespaced_custom_object.side_effect = ApiException(status=403, reason="forbidden")
        with patch("migrator.load_client", return_value=api):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "❌" in result.output

    def test_load_client_failure_exits_nonzero(self):
        with patch("migrator.load_client", side_effect=MigratorError("bad context")):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "bad context" in result.output

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
        api = self._mock_api(app=self._remote_app())
        mock_wc_api = MagicMock()
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", return_value=mock_wc_api) as mock_load_wc:
            result = self._run(self._args())
        mock_load_wc.assert_called_once()
        assert result.exit_code == 0

    def test_load_wc_client_failure_exits_nonzero(self):
        api = self._mock_api(app=self._remote_app())
        with patch("migrator.load_client", return_value=api), \
             patch("migrator.core_client", return_value=MagicMock()), \
             patch("migrator.load_wc_client", side_effect=MigratorError("bad kubeconfig secret")):
            result = self._run(self._args())
        assert result.exit_code != 0
        assert "bad kubeconfig secret" in result.output
