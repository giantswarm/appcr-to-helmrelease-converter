# 8. Three-command CLI structure: convert, convert-from-cluster, migrate

Date: 2026-05-20

## Status

Proposed

## Context

The original tool had one command (`convert`) that read a single App CR from stdin
and produced Flux YAML. With the addition of non-GS catalog support (requiring the
Catalog CR as a second input) and the longer-term goal of driving a full live
migration, the command needed to grow.

Two structural options were considered:

1. **One command with flags** — a single `convert` command gains `--name`,
   `--namespace`, `--context`, and live-migration flags over time. Simple surface,
   but flags from different capability levels mix together.
2. **Dedicated commands per capability level** — each command owns exactly the
   scope it needs; no flags bleed across levels.

Option 2 was chosen.

## Decision

Three commands, each a strict superset of the previous:

| Command | Inputs | Scope |
|---|---|---|
| `convert` | Multi-doc YAML from stdin or file (App CR + Catalog CR separated by `---`) | Offline pure transform; no cluster access |
| `convert-from-cluster` | `--name`, `--namespace`, optional `--context` | Fetch both CRs from MC, then convert |
| `migrate` | `--name`, `--namespace`, optional `--context` | Fetch + convert + apply + observe + optional revert _(future)_ |

`convert` and `convert-from-cluster` share the same `converter/` pure functions.
`convert-from-cluster` and `migrate` share the same `fetcher/` I/O package.

`migrate` is reserved as a named command but not yet implemented.

## Consequences

- Each command has a clear, minimal scope; no flags from one level leak into another.
- Offline users (e.g. in scripts, CI) can use `convert` without cluster credentials,
  as long as they supply both CRs in a multi-doc YAML.
- The `fetcher/` package is a shared dependency of `convert-from-cluster` and the
  future `migrate` command, so its interface is designed with both in mind.
- Removing `migrate` from scope now avoids premature design of the apply/observe/
  revert logic while still reserving the command name.
