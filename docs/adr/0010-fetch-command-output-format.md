# 10. fetch command emits raw multi-doc YAML with Catalog CR first

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

**Filtering:** Raw, unmodified output. The `fetch` command is an inspection and
scripting tool — stripping fields would hide information and could silently drop
data that downstream tools depend on. The `convert` command reads only the fields
it needs and ignores the rest, so server-side metadata in the stream is harmless.

## Consequences

- `fetch | convert` works regardless of document order, since `convert` identifies
  documents by `kind` + `apiVersion`.
- Users who want cleaned YAML (e.g. to commit a CR to git) must strip server-side
  fields themselves. This is a deliberate choice — `fetch` does not try to be a
  sanitisation tool.
- The Catalog-first ordering is a stable convention: scripts that parse `fetch`
  output by position can rely on it.
