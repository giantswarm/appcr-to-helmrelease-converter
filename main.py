import sys
from collections import OrderedDict

import click
import yaml
from yaml.resolver import BaseResolver

from converter import convert


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
    _dump(convert(input.read()))


cli.add_command(convert_cmd, name="convert")


if __name__ == "__main__":
    cli()
