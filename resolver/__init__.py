from dataclasses import dataclass, field

import click

from fetcher import iter_refs


class ResolverError(Exception):
    """A valuesKey could not be resolved non-interactively."""


@dataclass
class Resolution:
    key_overrides: dict[tuple[str, str], str | None] = field(default_factory=dict)
    optional: set[tuple[str, str]] = field(default_factory=set)


def resolve(
    app: dict,
    referenced_configs: dict,
    *,
    overrides: dict[tuple[str, str], str] | None = None,
    assume_yes: bool = False,
) -> Resolution:
    overrides = overrides or {}
    key_overrides = {}
    optional = set()
    seen = set()
    for kind, name, ns in iter_refs(app):
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        resource = referenced_configs.get((kind, name, ns))
        if resource is None:
            continue
        keys = list((resource.get("data") or {}).keys())
        if not keys:
            click.echo(
                f'{kind} "{name}" has no data keys — adding to valuesFrom as optional: true',
                err=True,
            )
            optional.add((kind, name))
            key_overrides[(kind, name)] = None
            continue
        if len(keys) == 1:
            chosen = keys[0]
            click.echo(f'{kind} "{name}" → {chosen} (auto)')
        elif (kind, name) in overrides:
            chosen = overrides[(kind, name)]
            if chosen not in keys:
                raise ResolverError(
                    f'{kind} "{name}": --values-key "{chosen}" is not a data key of the '
                    f'resource (available: {", ".join(keys)})'
                )
            click.echo(f'{kind} "{name}" → {chosen} (--values-key)')
        elif assume_yes:
            raise ResolverError(
                f'{kind} "{name}" has multiple keys ({", ".join(keys)}) and no --values-key '
                f'was given; pass --values-key {kind}/{name}=<key> to run non-interactively'
            )
        else:
            chosen = click.prompt(
                f'{kind} "{name}" has multiple keys — select valuesKey',
                type=click.Choice(keys),
            )
            click.echo(f'{kind} "{name}" → {chosen}')
        key_overrides[(kind, name)] = chosen
    return Resolution(key_overrides=key_overrides, optional=optional)
