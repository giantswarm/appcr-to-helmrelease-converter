# 15. Collapse CLI to single `migrate` command

Date: 2026-06-10

## Status

Implemented. Supersedes ADR 0008.

## Context

ADR 0008 established four commands at increasing capability levels: `convert`
(offline), `fetch` (MC read-only), `fetch-and-convert` (MC read + convert), and
`migrate` (full live migration).

The introduction of live valuesKey resolution (ADR 0016) requires looking up each
ConfigMap and Secret referenced by the App CR from the cluster to determine which
data key holds Helm values. This decision cannot be made from the YAML alone.
Maintaining an offline `convert` path would mean producing output with hardcoded
`valuesKey` values (`configmap-values.yaml` / `secret-values.yaml`) that may be
wrong. The cost of an inaccurate `valuesKey` is a broken HelmRelease after migration.

With `convert` requiring cluster access, `fetch` loses its primary purpose
(piping into `convert`), and `fetch-and-convert` becomes a subset of `migrate`
without the interactive resolver step.

## Decision

Remove `convert`, `fetch`, and `fetch-and-convert`. The tool has a single command:
`migrate`.

The `migrate` command already requires cluster access and drives the full live
migration. Inspecting the fetched App CR and Catalog CR moves into the `migrate`
display step (before the confirm prompt) rather than being a separate `fetch`
command output.

The `fetcher/` package is retained — it is still used by `migrate`.

## Consequences

- Cluster access is always required; there is no offline conversion path.
- All `valuesKey` decisions are made from real cluster data and are accurate.
- The codebase loses three commands and their tests; the overall surface shrinks.
- Users who previously used `convert` or `fetch-and-convert` in scripts or CI
  pipelines must switch to `migrate` or extract the Flux YAML from its output.
