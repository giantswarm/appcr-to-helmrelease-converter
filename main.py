import io
import sys
from collections import OrderedDict

import click
import yaml
from kubernetes.client.exceptions import ApiException
from rich.console import Console
from rich.syntax import Syntax
from yaml.resolver import BaseResolver

import fetcher
import migrator
import resolver
from converter import convert, override_catalog_registry
from converter.release_chart import (
    cluster_chart_name,
    INSTALLATION_VALUES_CONFIGMAP,
    cluster_chart_provider,
    has_installation_values,
    localize_installation_values,
    release_chart_name,
    release_cr_name,
    substitute_release_chart,
)
from fetcher import FetchError
from migrator.cleanup import delete_app_and_chart, flux_cleanup_message, verify_migration
from migrator.release_values import (
    RELEASE_VERSION_VALUE,
    find_release_version_sources,
    get_helm_release,
    remove_release_version,
)
from migrator.resume import resume_app_and_chart
from preflight import PreflightError, run_preflight


_SERVER_METADATA_FIELDS = frozenset({
    "creationTimestamp",
    "generation",
    "managedFields",
    "resourceVersion",
    "selfLink",
    "uid",
})

_SERVER_ANNOTATIONS = frozenset({
    "kubectl.kubernetes.io/last-applied-configuration",
})


def _strip_server_fields(doc: dict) -> dict:
    if "metadata" not in doc:
        return doc
    stripped = {k: v for k, v in doc["metadata"].items() if k not in _SERVER_METADATA_FIELDS}
    if "annotations" in stripped:
        annotations = {k: v for k, v in stripped["annotations"].items()
                       if k not in _SERVER_ANNOTATIONS}
        if annotations:
            stripped["annotations"] = annotations
        else:
            del stripped["annotations"]
    return {**doc, "metadata": stripped}


def _to_yaml_str(docs) -> str:
    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping(BaseResolver.DEFAULT_MAPPING_TAG, data.items())

    yaml.add_representer(OrderedDict, ordered_dict_representer)
    buf = io.StringIO()
    yaml.dump_all(docs, buf, sort_keys=False, default_flow_style=False)
    return "---\n" + buf.getvalue()


def _dump_highlighted(yaml_str: str):
    Console(file=sys.stdout, highlight=False).print(
        Syntax(yaml_str, "yaml", theme="monokai", word_wrap=True)
    )


@click.group()
def cli():
    pass


def _section(title: str) -> None:
    con = Console(file=sys.stdout, highlight=False)
    con.print()
    con.rule(f"[bold cyan]▌ {title}[/bold cyan]", align="left")


def _resolve_chart_api(api, app: dict):
    kube = (app.get("spec") or {}).get("kubeConfig") or {}
    if kube.get("inCluster") is False:
        secret = kube.get("secret") or {}
        secret_name = secret.get("name", "")
        secret_ns = secret.get("namespace") or app["metadata"]["namespace"]
        return migrator.load_wc_client(migrator.core_client(), secret_name, secret_ns)
    return api


def _load_client_or_exit(context: str | None):
    try:
        return migrator.load_client(context)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)


def _resolve_chart_api_or_exit(api, app: dict):
    try:
        return _resolve_chart_api(api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)


def _report_step_result(step) -> None:
    if step.skipped:
        click.echo(f"ℹ️  {step.skip_message}")
    elif step.revert_note is not None:
        click.echo(f"ℹ️  {step.description} — already set, nothing to do.")
    else:
        click.echo(f"✅ {step.description}")


def _get_app_or_exit(api, namespace: str, name: str, not_found_message: str) -> dict:
    try:
        return api.get_namespaced_custom_object(
            group=migrator._GROUP, version=migrator._VERSION,
            namespace=namespace, plural=migrator._PLURAL, name=name,
        )
    except ApiException as e:
        if e.status == 404:
            click.echo(f"ℹ️  {not_found_message}")
            raise SystemExit(0)
        click.echo(
            f"❌ failed to fetch App {namespace}/{name}: {migrator._api_message(e)}", err=True
        )
        raise SystemExit(1)


def _delete_and_report(api, chart_api, app: dict) -> None:
    try:
        notes = delete_app_and_chart(api, chart_api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    for note in notes:
        click.echo(f"ℹ️  {note}")
    if notes:
        click.echo("✅ App CR deleted.")
    else:
        click.echo("✅ App CR and Chart CR deleted.")


def _release_version_leftovers_or_exit(helm_release: dict) -> list:
    try:
        return find_release_version_sources(migrator.core_client(), helm_release)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)


def _announce_release_version_removal(leftovers) -> None:
    for source in leftovers:
        click.echo(
            f"Will remove {RELEASE_VERSION_VALUE} from {source} — the Release chart carries it. "
            f"The YAML under that key is rewritten; comments are not preserved."
        )


def _delete_prompt(leftovers) -> str:
    if leftovers:
        return f"Remove {RELEASE_VERSION_VALUE} and delete App CR and Chart CR?"
    return "Delete App CR and Chart CR?"


def _remove_release_version_and_report(leftovers) -> None:
    core_api = migrator.core_client()
    for source in leftovers:
        try:
            remove_release_version(core_api, source)
        except migrator.MigratorError as e:
            click.echo(f"❌ {e}", err=True)
            raise SystemExit(1)
        click.echo(f"✅ Removed {RELEASE_VERSION_VALUE} from {source}.")


def _resume_and_report(api, chart_api, app: dict) -> None:
    try:
        notes = resume_app_and_chart(api, chart_api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    for note in notes:
        click.echo(f"ℹ️  {note}")
    if notes:
        click.echo("✅ App CR unpaused.")
    else:
        click.echo("✅ App CR and Chart CR unpaused.")


def _host_only(value: str) -> str:
    return value.rpartition("://")[2]


def _validate_registry_override(ctx, param, value):
    if value is None:
        return None
    value = value.removesuffix("/")
    host = _host_only(value)
    if not host or any(c in host for c in "/?#"):
        raise click.BadParameter(
            f'"{value}" is not a registry host; pass a host like gsociprivate.azurecr.io '
            "or registry.local:5000, optionally with a scheme, but without a path, query, or fragment",
            param_hint="--override-registry-url",
        )
    return value


def _parse_values_keys(entries) -> dict[tuple[str, str], str]:
    overrides = {}
    for entry in entries:
        left, sep, key = entry.partition("=")
        kind, slash, cfg_name = left.partition("/")
        if not sep or not slash or not kind or not cfg_name or not key:
            raise click.BadParameter(
                f'"{entry}" is not in KIND/NAME=KEY form, e.g. ConfigMap/my-cm=values.yaml',
                param_hint="--values-key",
            )
        overrides[(kind, cfg_name)] = key
    return overrides


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--output", "output_file", type=click.Path(writable=True, dir_okay=False), default=None)
@click.option(
    "--assume-yes", "-y", "assume_yes", is_flag=True, default=False,
    help="Auto-confirm the proceed and revert-on-failure prompts (never the destructive "
         "delete of App/Chart CRs — that's this command's own prompt; the sibling "
         "`cleanup` command's -y does skip its delete prompt). Combine with --values-key "
         "for a fully non-interactive run.",
)
@click.option(
    "--values-key", "values_key", multiple=True, metavar="KIND/NAME=KEY",
    help="Pre-answer the valuesKey prompt for a multi-key ConfigMap/Secret, "
         "e.g. --values-key ConfigMap/my-cm=values.yaml. Repeatable.",
)
@click.option(
    "--pull-secret", "pull_secret", default=None, metavar="NAME",
    help="Name of an existing Secret holding catalog credentials. Emitted as spec.secretRef "
         "on the generated OCIRepository or HelmRepository. Must be in the App CR's namespace "
         "— Flux does not resolve it across namespaces.",
)
@click.option(
    "--override-registry-url", "registry_override", default=None, metavar="HOST",
    callback=_validate_registry_override,
    help="Replace the registry host of the catalog URL, e.g. gsociprivate.azurecr.io. "
         "Host and optional port only — the chart path from the Catalog CR is kept. "
         "Use it when the installation pulls charts from a mirror of the catalog's registry.",
)
@click.option(
    "--ignore-app-status", "ignore_app_status", is_flag=True, default=False,
    help="Migrate even when the App CR is not deployed (release status other than deployed, or "
         "status.version differing from spec.version). Skips only that check.",
)
def migrate_cmd(name, namespace, context, dry_run, output_file, assume_yes, values_key, pull_secret,
                registry_override, ignore_app_status):
    _section("Fetch")
    click.echo(f'Fetching "{name}" from namespace "{namespace}"...')
    try:
        result = fetcher.fetch(name, namespace, context, pull_secret)
    except FetchError as e:
        click.echo("", err=True)
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    app, catalog = result.app, result.catalog
    _dump_highlighted(_to_yaml_str([_strip_server_fields(app)]))

    release_chart = result.release_chart
    localized = bool(release_chart) and has_installation_values(app)
    source_app = localize_installation_values(app) if localized else app

    _section("Preflight checks")
    issues = run_preflight(
        source_app, catalog, result.referenced_configs, result.dependency_helm_releases,
        pull_secret_name=pull_secret, pull_secret=result.pull_secret,
        registry_override=registry_override,
        ignore_app_status=ignore_app_status,
        release_chart=result.release_chart,
    )
    warnings = [i for i in issues if not isinstance(i, PreflightError)]
    errors = [i for i in issues if isinstance(i, PreflightError)]
    for issue in warnings + errors:
        click.echo(issue.display(), err=True)
    if errors:
        raise SystemExit(1)
    if result.dependency_helm_releases:
        ns = app.get("metadata", {}).get("namespace", "")
        checked = ", ".join(f"{ns}/{name}" for name in result.dependency_helm_releases)
        click.echo(
            f"ℹ️  Dependency check passed for HelmRelease `{checked}` based on `app-operator.giantswarm.io/depends-on` annotation of App CR. Verifies existence only — readiness is enforced by Flux at runtime.",
            err=True,
        )
    if localized:
        click.echo(
            f"ℹ️  {INSTALLATION_VALUES_CONFIGMAP} read from its copy in {namespace} (synced there by "
            f"Kyverno) instead of giantswarm — Flux can only read values from the HelmRelease's namespace.",
            err=True,
        )
    if release_chart:
        provider, release_version = release_chart.provider, release_chart.release_version
        click.echo(
            f"ℹ️  Release chart substitution: {cluster_chart_name(provider)}@{app['spec']['version']} → "
            f"{release_chart_name(provider)}@{release_version} "
            f"(Release CR {release_cr_name(provider, release_version)})",
            err=True,
        )
    if not issues:
        click.echo("No issues found")
    else:
        click.echo(f"{len(warnings)} warning(s), 0 errors")

    _section("Resolve")
    try:
        resolution = resolver.resolve(
            source_app,
            result.referenced_configs,
            overrides=_parse_values_keys(values_key),
            assume_yes=assume_yes,
        )
    except resolver.ResolverError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    _section("Generated Flux resources")
    if registry_override:
        catalog = override_catalog_registry(catalog, registry_override)
        click.echo(f"ℹ️  Registry host overridden to {_host_only(registry_override)}.")
    if release_chart:
        source_app = substitute_release_chart(source_app, release_chart.provider, release_chart.release_version)
    docs = convert(source_app, catalog, resolution, pull_secret)
    yaml_str = _to_yaml_str(docs)
    _dump_highlighted(yaml_str)

    if output_file:
        with open(output_file, "w") as f:
            f.write(yaml_str)
        click.echo(f"\n✅ Conversion result saved to {output_file}!")

    if dry_run:
        click.echo("\n✅ Dry run complete — halting before live migration.")
        raise SystemExit(0)

    _section("Confirm")
    if assume_yes:
        click.echo("Proceeding with live migration (--assume-yes).")
    elif not click.confirm("Proceed with live migration?"):
        raise SystemExit(0)

    _section(f"Suspend {namespace}/{name}")
    api = _load_client_or_exit(context)

    runner = migrator.MigrationRunner()
    console = Console(file=sys.stdout, highlight=False)
    chart_api = _resolve_chart_api_or_exit(api, app)
    steps = [
        migrator.DisableFluxReconcileApp(api, app),
        migrator.SuspendApp(api, app),
        migrator.SuspendChart(chart_api, app),
        migrator.ApplyFluxResources(api, docs),
        migrator.MonitorHelmRelease(api, docs[1], console=console),
    ]

    for step in steps:
        try:
            runner.run(step)
        except migrator.MigratorError as e:
            click.echo(f"❌ {e}", err=True)
            if assume_yes or click.confirm("Revert changes?", default=True):
                click.echo("Reverting...", err=True)
                try:
                    runner.revert_all()
                except migrator.MigratorError as revert_err:
                    click.echo(f"❌ revert failed: {revert_err}", err=True)
                    click.echo("Some changes may need to be reverted manually.", err=True)
            else:
                click.echo("Skipping revert. Manual steps required to undo changes.", err=True)
            for note in runner.revert_notes:
                click.echo(note, err=True)
            raise SystemExit(1)
        _report_step_result(step)

    _section("Clean-up")
    if migrator.is_flux_managed(app):
        click.echo(flux_cleanup_message(app, context))
    else:
        leftovers = _release_version_leftovers_or_exit(docs[1]) if release_chart else []
        _announce_release_version_removal(leftovers)
        if click.confirm(_delete_prompt(leftovers), default=False):
            _remove_release_version_and_report(leftovers)
            _delete_and_report(api, chart_api, app)


cli.add_command(migrate_cmd, name="migrate")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option(
    "--assume-yes", "-y", "assume_yes", is_flag=True, default=False,
    help="Auto-confirm the delete prompt. Skips no safety check.",
)
def cleanup_cmd(name, namespace, context, dry_run, assume_yes):
    _section("Fetch")
    api = _load_client_or_exit(context)

    app = _get_app_or_exit(
        api, namespace, name, f"App {namespace}/{name} not found — nothing to clean up."
    )

    _section("Verify")
    failures = verify_migration(api, app)
    leftovers = []
    if not failures and cluster_chart_provider(app):
        try:
            leftovers = find_release_version_sources(migrator.core_client(), get_helm_release(api, app))
        except migrator.MigratorError as e:
            failures.append(str(e))
        if leftovers and migrator.is_flux_managed(app):
            failures += [
                f"{source} still sets {RELEASE_VERSION_VALUE} — remove it from your gitops repository, "
                f"let Flux reconcile, then re-run"
                for source in leftovers
            ]
    if failures:
        for failure in failures:
            click.echo(f"❌ {failure}", err=True)
        raise SystemExit(1)
    click.echo("✅ Migration verified — HelmRelease is ready and adopted.")

    _section("Delete")
    chart_api = _resolve_chart_api_or_exit(api, app)

    chart_name = migrator.chart_cr_name(app)
    click.echo(
        f"Will delete Chart {migrator._CHART_NAMESPACE}/{chart_name} and App {namespace}/{name}."
    )
    if migrator.is_flux_managed(app):
        click.echo(
            "⚠️  This does not verify the App CR was removed from your gitops repo. "
            "If it is still there, Flux will re-apply it."
        )
    _announce_release_version_removal(leftovers)

    if dry_run:
        click.echo("\n✅ Dry run complete — halting before deletion.")
        raise SystemExit(0)

    if not (assume_yes or click.confirm(_delete_prompt(leftovers), default=False)):
        raise SystemExit(0)

    _remove_release_version_and_report(leftovers)
    _delete_and_report(api, chart_api, app)


cli.add_command(cleanup_cmd, name="cleanup")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
def suspend_cmd(name, namespace, context):
    _section("Fetch")
    api = _load_client_or_exit(context)

    app = _get_app_or_exit(
        api, namespace, name,
        f"App {namespace}/{name} not found — nothing to suspend "
        f"(can't look up the Chart CR without it).",
    )

    _section("Suspend")
    chart_api = _resolve_chart_api_or_exit(api, app)

    for step in (
        migrator.SuspendApp(api, app),
        migrator.SuspendChart(chart_api, app, tolerate_missing=True),
    ):
        try:
            step.apply()
        except migrator.MigratorError as e:
            click.echo(f"❌ {e}", err=True)
            raise SystemExit(1)
        _report_step_result(step)


cli.add_command(suspend_cmd, name="suspend")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
def resume_cmd(name, namespace, context):
    _section("Fetch")
    api = _load_client_or_exit(context)

    app = _get_app_or_exit(
        api, namespace, name,
        f"App {namespace}/{name} not found — nothing to resume "
        f"(can't look up the Chart CR without it).",
    )

    _section("Resume")
    chart_api = _resolve_chart_api_or_exit(api, app)

    _resume_and_report(api, chart_api, app)


cli.add_command(resume_cmd, name="resume")


if __name__ == "__main__":
    cli()
