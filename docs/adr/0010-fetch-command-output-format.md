# 10. fetch command emits cleaned multi-doc YAML with Catalog CR first

Date: 2026-05-20

## Status

Accepted

## Context

The `fetch` command pulls an App CR and its Catalog CR from the MC and must emit
them as YAML. Two output decisions were required:

**Document order:**
1. App CR first, Catalog CR second — mirrors the mental model of "I'm working with
   an App, and its catalog is secondary context".
2. Catalog CR first, App CR second — mirrors the input order that `convert` expects
   internally, and makes the dependency relationship explicit (Catalog is looked up
   first during fetch).

**Field filtering:**
1. Strip server-side metadata (`resourceVersion`, `uid`, `managedFields`,
   `creationTimestamp`, etc.) before emitting — cleaner output, closer to what a
   user would author by hand.
2. Emit raw, unmodified — full fidelity to what the cluster returned; no risk of
   accidentally dropping a field the user or a downstream tool needs.

## Decision

**Order:** Catalog CR first, App CR second. The Catalog CR is fetched first
(it is a dependency of the App CR lookup), and the output order reflects that.
The `convert` command identifies documents by `kind` + `apiVersion`, not position,
so the ordering has no effect on `fetch | convert` pipelines.

**Filtering:** Strip well-known server-side fields before emitting. The following
`metadata` fields are removed: `uid`, `resourceVersion`, `generation`,
`creationTimestamp`, `selfLink`, `managedFields`. The annotation
`kubectl.kubernetes.io/last-applied-configuration` is also removed; if that was the
only annotation, the `annotations` key is dropped entirely. All other fields
(labels, other annotations, spec, status) are preserved unchanged.

Stripping happens in the CLI output layer (`main.py`), not in the `fetcher/`
package. The `fetcher/` package always returns the full server response so that
future commands (e.g. `migrate`) that need `resourceVersion` for optimistic
concurrency can use the raw data without a second fetch.

## Consequences

- `fetch | convert` works regardless of document order, since `convert` identifies
  documents by `kind` + `apiVersion`.
- `fetch` output is immediately usable: clean enough to commit to git, pipe into
  other tools, or read by hand, without a manual stripping step.
- The `fetcher/` package remains a faithful I/O layer; field stripping is a
  presentation decision owned by the CLI.
- The Catalog-first ordering is a stable convention: scripts that parse `fetch`
  output by position can rely on it.
