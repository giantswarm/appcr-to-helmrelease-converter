import io
import sys
from collections import OrderedDict

import click
import yaml
from rich.console import Console
from rich.syntax import Syntax
from yaml.resolver import BaseResolver

import fetcher
import migrator
import resolver
from converter import convert
from fetcher import FetchError
from migrator.cleanup import delete_app_and_chart, flux_cleanup_message
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


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--output", "output_file", type=click.Path(writable=True, dir_okay=False), default=None)
def migrate_cmd(name, namespace, context, dry_run, output_file):
    _section("Fetch")
    click.echo(f'Fetching "{name}" from namespace "{namespace}"...')
    try:
        result = fetcher.fetch(name, namespace, context)
    except FetchError as e:
        click.echo("", err=True)
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    app, catalog = result.app, result.catalog
    _dump_highlighted(_to_yaml_str([_strip_server_fields(app)]))

    _section("Preflight checks")
    issues = run_preflight(app, catalog, result.referenced_configs, result.dependency_helm_releases)
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
    resolution = resolver.resolve(app, result.referenced_configs)

    _section("Generated Flux resources")
    docs = convert(app, catalog, resolution)
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
    if not click.confirm("Proceed with live migration?"):
        raise SystemExit(0)

    _section(f"Suspend {namespace}/{name}")
    try:
        api = migrator.load_client(context)
    except migrator.MigratorError as e:
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)

    runner = migrator.MigrationRunner()
    console = Console(file=sys.stdout, highlight=False)
    kube = (app.get("spec") or {}).get("kubeConfig") or {}
    if kube.get("inCluster") is False:
        secret = kube.get("secret") or {}
        secret_name = secret.get("name", "")
        secret_ns = secret.get("namespace") or namespace
        try:
            chart_api = migrator.load_wc_client(migrator.core_client(), secret_name, secret_ns)
        except migrator.MigratorError as e:
            click.echo(f"❌ {e}", err=True)
            raise SystemExit(1)
    else:
        chart_api = api
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
            if click.confirm("Revert changes?", default=True):
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
    labels = (app.get("metadata") or {}).get("labels") or {}
    is_flux_managed = (
        migrator._FLUX_NAME_LABEL in labels
        and migrator._FLUX_NS_LABEL in labels
    )
    if is_flux_managed:
        click.echo(flux_cleanup_message(app))
    else:
        if click.confirm("Delete App CR and Chart CR?", default=False):
            try:
                delete_app_and_chart(api, chart_api, app)
            except migrator.MigratorError as e:
                click.echo(f"❌ {e}", err=True)
                raise SystemExit(1)
            click.echo("✅ App CR and Chart CR deleted.")


cli.add_command(migrate_cmd, name="migrate")


if __name__ == "__main__":
    cli()
