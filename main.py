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
from converter import convert
from fetcher import FetchError
from migrator.cleanup import delete_app_and_chart, flux_cleanup_message, verify_migration
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


def _delete_and_report(api, chart_api, app: dict) -> None:
    try:
        notes = delete_app_and_chart(api, chart_api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    for note in notes:
        click.echo(f"ℹ️  {note}")
    click.echo("✅ App CR and Chart CR deleted.")


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
def migrate_cmd(name, namespace, context, dry_run, output_file, assume_yes, values_key, pull_secret):
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

    _section("Preflight checks")
    issues = run_preflight(
        app, catalog, result.referenced_configs, result.dependency_helm_releases,
        pull_secret_name=pull_secret, pull_secret=result.pull_secret,
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
    if not issues:
        click.echo("No issues found")
    else:
        click.echo(f"{len(warnings)} warning(s), 0 errors")

    _section("Resolve")
    try:
        resolution = resolver.resolve(
            app,
            result.referenced_configs,
            overrides=_parse_values_keys(values_key),
            assume_yes=assume_yes,
        )
    except resolver.ResolverError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    _section("Generated Flux resources")
    docs = convert(app, catalog, resolution, pull_secret)
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
    try:
        api = migrator.load_client(context)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    runner = migrator.MigrationRunner()
    console = Console(file=sys.stdout, highlight=False)
    try:
        chart_api = _resolve_chart_api(api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
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
        if step.skipped:
            click.echo(f"ℹ️ {step.skip_message}")
        else:
            click.echo(f"✅ {step.description}")

    _section("Clean-up")
    if migrator.is_flux_managed(app):
        click.echo(flux_cleanup_message(app, context))
    else:
        if click.confirm("Delete App CR and Chart CR?", default=False):
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
    try:
        api = migrator.load_client(context)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    try:
        app = api.get_namespaced_custom_object(
            group=migrator._GROUP, version=migrator._VERSION,
            namespace=namespace, plural=migrator._PLURAL, name=name,
        )
    except ApiException as e:
        if e.status == 404:
            click.echo(f"ℹ️  App {namespace}/{name} not found — nothing to clean up.")
            raise SystemExit(0)
        click.echo(
            f"❌ failed to fetch App {namespace}/{name}: {migrator._api_message(e)}", err=True
        )
        raise SystemExit(1)

    _section("Verify")
    failures = verify_migration(api, app)
    if failures:
        for failure in failures:
            click.echo(f"❌ {failure}", err=True)
        raise SystemExit(1)
    click.echo("✅ Migration verified — HelmRelease is ready and adopted.")

    _section("Delete")
    try:
        chart_api = _resolve_chart_api(api, app)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    chart_name = migrator.chart_cr_name(app)
    click.echo(
        f"Will delete Chart {migrator._CHART_NAMESPACE}/{chart_name} and App {namespace}/{name}."
    )
    if migrator.is_flux_managed(app):
        click.echo(
            "⚠️  This does not verify the App CR was removed from your gitops repo. "
            "If it is still there, Flux will re-apply it."
        )

    if dry_run:
        click.echo("\n✅ Dry run complete — halting before deletion.")
        raise SystemExit(0)

    if not (assume_yes or click.confirm("Delete App CR and Chart CR?", default=False)):
        raise SystemExit(0)

    _delete_and_report(api, chart_api, app)


cli.add_command(cleanup_cmd, name="cleanup")


if __name__ == "__main__":
    cli()
