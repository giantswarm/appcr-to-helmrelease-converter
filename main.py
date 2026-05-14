import sys
from collections import OrderedDict

import click
import yaml
from yaml.resolver import BaseResolver

from converter import convert
from preflight import PreflightError, run_preflight


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


if __name__ == "__main__":
    cli()
