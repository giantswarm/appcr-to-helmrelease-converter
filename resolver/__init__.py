from dataclasses import dataclass, field

import click

from fetcher import iter_refs


@dataclass
class Resolution:
    key_overrides: dict[tuple[str, str], str | None] = field(default_factory=dict)


def resolve(app: dict, referenced_configs: dict) -> Resolution:
    key_overrides = {}
    seen = set()
    for kind, name, ns in iter_refs(app):
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        resource = referenced_configs.get((kind, name, ns))
        if resource is None:
            continue
        keys = list((resource.get("data") or {}).keys())
        if len(keys) == 1:
            chosen = keys[0]
            click.echo(f'{kind} "{name}" → {chosen} (auto)')
        else:
            chosen = click.prompt(
                f'{kind} "{name}" has multiple keys — select valuesKey',
                type=click.Choice(keys),
            )
            click.echo(f'{kind} "{name}" → {chosen}')
        key_overrides[(kind, name)] = chosen
    return Resolution(key_overrides=key_overrides)
