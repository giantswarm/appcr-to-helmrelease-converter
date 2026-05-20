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


@cli.command()
@click.argument("input", type=click.File("r"), default="-")
def convert_cmd(input):
    content = input.read()
    app = yaml.safe_load(content)
    issues = run_preflight(app)
    for issue in issues:
        level = "error" if isinstance(issue, PreflightError) else "warning"
        click.echo(f"{level}: {issue}", err=True)
    if any(isinstance(i, PreflightError) for i in issues):
        raise SystemExit(1)
    _dump(convert(content))


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
