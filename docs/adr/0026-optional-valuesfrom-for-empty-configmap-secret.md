# 26. Mark valuesFrom entries optional when the source has no data keys

Date: 2026-07-23

## Status

Accepted

## Context

`resolver.resolve()` picks a `valuesKey` from a referenced ConfigMap/Secret's
`data` keys. It only handled two cases explicitly: exactly one key
(auto-resolve) and multiple keys (prompt, `--values-key`, or `--assume-yes`
hard error). A resource with **zero** data keys fell through into the
multiple-keys branches instead:

- with `--assume-yes` and no override: raised `ResolverError` with a
  nonsensical "has multiple keys ()" message
- interactively: called `click.prompt(..., type=click.Choice([]))`, which
  has no valid choice to ever accept
- with `--values-key` supplied: raised `ResolverError` ("not a data key of
  the resource")

An empty ConfigMap/Secret is a legitimate real-world case — e.g. a
`userConfig` secret that exists but was never populated. This isn't a
resolution failure the operator needs to answer for; the migration
shouldn't stop here.

Separately, `preflight.check_empty_referenced_configs()` (added earlier,
independent of `resolve()`) already hard-failed with the same root cause: it
raised a `PreflightError` for any referenced_config with no `data` keys.
Preflight runs before Resolve in the `migrate` pipeline (see ADR 0016), so
this check stopped the migration before `resolve()`'s fix could ever be
reached — fixing `resolve()` alone was not sufficient.

## Decision

`resolve()` now checks for zero keys before the single/multiple-key logic
and handles it uniformly regardless of `--values-key`/`--assume-yes`/
interactive mode: it prints a warning, records the reference as optional,
and moves on without a `valuesKey`.

`check_empty_referenced_configs()` is downgraded from `PreflightError` to
`PreflightWarning`, with its message updated to describe the new outcome
("valuesFrom entry will be marked optional: true") instead of the old
"cannot determine valuesKey" framing — it's no longer a blocking condition.

`Resolution` gains an `optional: set[tuple[str, str]]` field alongside the
existing `key_overrides`. `converter/values_from.py`'s
`to_reference_with_priority()` adds `optional: true` to the generated
`valuesFrom` entry when the (kind, name) pair is in that set — no
`valuesKey` is emitted for these entries since there's no key to point at.

`optional: true` is a real field on Flux's `ValuesReference` type: it tells
helm-controller to ignore a "values file not found" error at that reference
instead of failing the HelmRelease. That's exactly the failure mode an empty
ConfigMap/Secret produces, so it's the correct signal to carry forward
rather than working around it in this tool.

## Consequences

- ConfigMap/Secret references with zero data keys no longer block
  migration at either preflight or Resolve — they still appear in the
  HelmRelease's `valuesFrom`, marked `optional: true`, with a warning
  printed during both steps.
- If the resource is later populated with data, the HelmRelease already
  references it correctly (still `optional: true`, which is harmless once
  data exists — the reference just resolves instead of being skipped).
- No `--strict`-style flag to turn this into a hard failure; it wasn't a
  failure case for app-operator either, so there was nothing to preserve.
