# 23. Non-interactive flags: --assume-yes and --values-key

Date: 2026-07-02

## Status

Accepted.

## Context

The `migrate` command has two interactive touch points (see ADR 0012, ADR 0016):

- **Confirm gates** — `click.confirm` at "Proceed with live migration?", "Revert changes?"
  (on step failure), and "Delete App CR and Chart CR?" (non-Flux cleanup).
- **valuesKey selection** — `click.prompt` when a referenced ConfigMap/Secret has more
  than one data key (ADR 0016).

A migration campaign runs the tool many times across a fleet (once per live App CR
instance, plus a staging run per app type). Driving prompts through a pty/stdin pipe is
fragile — answer-matching breaks across tool versions and the run's only record is the
transcript. The tool needs a deterministic, scriptable mode while keeping a human gate on
irreversible actions.

## Decision

Add two orthogonal flags to `migrate`:

### `--assume-yes` / `-y`

Auto-answers the **non-destructive** confirm gates:

- "Proceed with live migration?" → proceeds, printing `Proceeding with live migration (--assume-yes).`
- "Revert changes?" (on step failure) → takes the safe default (revert), same as the
  interactive default of `True`.

It deliberately does **not** auto-confirm the destructive "Delete App CR and Chart CR?"
prompt. That deletion removes finalizers and deletes cluster resources; it stays an
explicit human decision regardless of `--assume-yes`. In practice Flux-managed apps never
reach that prompt (they print manual cleanup instructions instead — ADR 0012), so scripted
runs against gitops-managed apps are already fully non-interactive.

### `--values-key KIND/NAME=KEY` (repeatable)

Pre-answers the resolver's multi-key prompt for a specific resource, e.g.
`--values-key ConfigMap/my-cm=values.yaml`. The resolver:

- uses the override for a multi-key resource instead of prompting;
- errors (`ResolverError`) if the given key is not an actual data key of the resource;
- ignores an override for a single-key resource (the sole key auto-resolves as before).

Under `--assume-yes`, a multi-key resource **without** a matching `--values-key` is a hard
error (`ResolverError`) rather than a hang — the message names the exact
`--values-key KIND/NAME=<key>` to supply.

Malformed `--values-key` values (not `KIND/NAME=KEY`) raise `click.BadParameter`.

### Intended workflow

The first run per app type stays interactive to discover the correct keys; the printed
`(--values-key)` / auto lines and the chosen answers become the replay flags for all
subsequent scripted runs of that app.

## Consequences

- `migrate` can run fully unattended for Flux-managed apps: `--assume-yes` plus a
  `--values-key` for each ambiguous resource.
- Irreversible CR deletion is never automated by a flag; it remains interactive (and, for
  Flux-managed apps, out-of-band per ADR 0012). _Amended by ADR 0027: this invariant is
  scoped to `migrate`. The `cleanup` command's `-y` does skip its delete prompt — there,
  deletion is the command's entire purpose rather than a trailing side effect, and it is
  gated behind six verification checks that no flag can skip._
- `resolver.resolve()` gains keyword-only `overrides` and `assume_yes` parameters and a new
  `ResolverError`; existing callers and the interactive path are unchanged when neither is
  passed.
