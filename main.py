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
from converter import convert
from fetcher import FetchError
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


def _dump(docs):
    print(_to_yaml_str(docs), end="")


def _dump_highlighted(docs):
    Console(file=sys.stdout, highlight=False).print(
        Syntax(_to_yaml_str(docs), "yaml", theme="monokai", word_wrap=True)
    )


@click.group()
def cli():
    pass


_GS_API_VERSION = "application.giantswarm.io/v1alpha1"


def _identify_docs(content: str) -> tuple[dict, dict]:
    app, catalog = None, None
    for doc in yaml.safe_load_all(content):
        if doc is None:
            continue
        if doc.get("apiVersion") != _GS_API_VERSION:
            continue
        kind = doc.get("kind")
        if kind == "App":
            if app is not None:
                raise ValueError("duplicate App CR in input")
            app = doc
        elif kind == "Catalog":
            if catalog is not None:
                raise ValueError("duplicate Catalog CR in input")
            catalog = doc
    if app is None:
        raise ValueError("App CR not found in input")
    if catalog is None:
        raise ValueError("Catalog CR not found in input")
    return app, catalog


def _section(title: str) -> None:
    con = Console(file=sys.stdout, highlight=False)
    con.print()
    con.rule(f"[bold cyan]▌ {title}[/bold cyan]", align="left")


def _check_and_emit(app: dict, catalog: dict) -> None:
    issues = run_preflight(app, catalog)
    warnings = [i for i in issues if not isinstance(i, PreflightError)]
    errors = [i for i in issues if isinstance(i, PreflightError)]
    for issue in warnings + errors:
        click.echo(issue.display(), err=True)
    if errors:
        raise SystemExit(1)


@cli.command()
@click.argument("input", type=click.File("r"), default="-")
def convert_cmd(input):
    content = input.read()
    try:
        app, catalog = _identify_docs(content)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(1)
    _check_and_emit(app, catalog)
    _dump(convert(app, catalog))


cli.add_command(convert_cmd, name="convert")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
def fetch_cmd(name, namespace, context):
    try:
        app, catalog = fetcher.fetch(name, namespace, context)
    except FetchError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(1)
    _dump([_strip_server_fields(catalog), _strip_server_fields(app)])


cli.add_command(fetch_cmd, name="fetch")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
def fetch_and_convert_cmd(name, namespace, context):
    try:
        app, catalog = fetcher.fetch(name, namespace, context)
    except FetchError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(1)
    _check_and_emit(app, catalog)
    _dump(convert(app, catalog))


cli.add_command(fetch_and_convert_cmd, name="fetch-and-convert")


@cli.command()
@click.option("--name", required=True)
@click.option("--namespace", required=True)
@click.option("--context", "context", default=None)
def migrate_cmd(name, namespace, context):
    _section("Fetch")
    click.echo(f'Fetching "{name}" from namespace "{namespace}"...')
    try:
        app, catalog = fetcher.fetch(name, namespace, context)
    except FetchError as e:
        click.echo("", err=True)
        click.echo(f"❌ {e}", err=True)
        raise SystemExit(1)
    app_version = app.get("spec", {}).get("version", "")
    app_catalog = app.get("spec", {}).get("catalog", "")
    catalog_meta_name = catalog.get("metadata", {}).get("name", "")
    repos = (catalog.get("spec") or {}).get("repositories") or []
    catalog_type = repos[0].get("type", "unknown") if repos else "unknown"
    click.echo("")
    click.echo(f"App CR:     {name} (version: {app_version}, catalog: {app_catalog})")
    click.echo("")
    click.echo(f"Catalog CR: {catalog_meta_name} (type: {catalog_type})")

    _section("Preflight checks")
    issues = run_preflight(app, catalog)
    warnings = [i for i in issues if not isinstance(i, PreflightError)]
    errors = [i for i in issues if isinstance(i, PreflightError)]
    for issue in warnings + errors:
        click.echo(issue.display(), err=True)
    if errors:
        raise SystemExit(1)
    if not issues:
        click.echo("No issues found")
    else:
        click.echo(f"{len(warnings)} warning(s), 0 errors")

    _section("Generated Flux resources")
    docs = convert(app, catalog)
    _dump_highlighted(docs)

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
    kube = (app.get("spec") or {}).get("kubeConfig") or {}
    if kube.get("inCluster") is False:
        secret = kube.get("secret") or {}
        secret_name = secret.get("name", "")
        secret_ns = secret.get("namespace") or namespace
        chart_api = migrator.load_wc_client(migrator.core_client(), secret_name, secret_ns)
    else:
        chart_api = api
    steps = [
        migrator.DisableFluxReconcileApp(api, app),
        migrator.SuspendApp(api, app),
        migrator.SuspendChart(chart_api, app),
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
            raise SystemExit(1)
        if step.skipped:
            click.echo(f"ℹ️ {step.skip_message}")
        else:
            click.echo(f"✅ {step.description}")


cli.add_command(migrate_cmd, name="migrate")


if __name__ == "__main__":
    cli()
