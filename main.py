import sys
from collections import OrderedDict

import click
import yaml
from yaml.resolver import BaseResolver

import fetcher
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


def _dump(docs):
    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping(BaseResolver.DEFAULT_MAPPING_TAG, data.items())

    yaml.add_representer(OrderedDict, ordered_dict_representer)
    print("---")
    yaml.dump_all(docs, sys.stdout, sort_keys=False, default_flow_style=False)


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


def _count_helm_repos(catalog: dict) -> int:
    return sum(
        1 for repo in (catalog.get("spec") or {}).get("repositories") or []
        if repo.get("type") == "helm"
    )


@cli.command()
@click.argument("input", type=click.File("r"), default="-")
def convert_cmd(input):
    content = input.read()
    try:
        app, catalog = _identify_docs(content)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(1)
    if _count_helm_repos(catalog) > 1:
        click.echo("warning: catalog has multiple helm repositories; using the first", err=True)
    issues = run_preflight(app)
    for issue in issues:
        level = "error" if isinstance(issue, PreflightError) else "warning"
        click.echo(f"{level}: {issue}", err=True)
    if any(isinstance(i, PreflightError) for i in issues):
        raise SystemExit(1)
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


if __name__ == "__main__":
    cli()
