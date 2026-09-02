# 31. `--override-registry-url` for mirrored registries

Date: 2026-09-02

## Status

Accepted

## Context

The converter reads the chart registry out of the Catalog CR and nowhere else.
`_oci_url_from_catalog` takes the first `spec.repositories[]` entry of `type:
oci` (falling back to `spec.storage`), and `build_oci_repository` appends the
chart name to it: `oci://gsoci.azurecr.io/charts/giantswarm` plus `nginx-proxy`
gives `oci://gsoci.azurecr.io/charts/giantswarm/nginx-proxy`. Path B does the
same through `_helm_url_from_catalog` and `_build_helm_repository`.

That is the right source of truth, and sometimes it is the wrong answer. An
installation can pull charts from a mirror of the Giant Swarm registry — an
air-gapped copy, a customer-owned registry, a private ACR such as
`gsociprivate.azurecr.io` — while the Catalog CR on the cluster still names
`gsoci.azurecr.io`. app-operator resolves this today through machinery outside
the Catalog CR, so the mirror is invisible to a tool that reads only the CR.
The generated OCIRepository then points at a registry `source-controller`
cannot reach, and the operator has to hand-edit the YAML after every run —
exactly the manual step this tool exists to remove.

The mirror copies the registry, not the chart layout. `charts/giantswarm/` and
the chart name are the same on both sides; only the host differs. Anything that
rewrote the whole base URL would make the operator restate a path the Catalog
CR already holds, and get it wrong when catalogs disagree on the path.

## Decision

Add `--override-registry-url HOST` to `migrate`. It replaces the **host** of
the one registry URL the converter reads. The scheme and the path are not the
operator's to supply. Without the flag nothing changes.

The other commands do not get it. `cleanup`, `suspend` and `resume` convert
nothing.

**The value.** A bare host (`gsociprivate.azurecr.io`), a host with a port
(`registry.local:5000`), or either of those with a scheme
(`oci://gsociprivate.azurecr.io`). A `click` option callback rejects a value
that carries a path, and an empty value, with `BadParameter` at parse time —
a path silently contradicts "host only", and a wrong URL that reaches the
cluster is worse than a usage error. One trailing slash is stripped first:
it carries no path segment, and `build_oci_repository` already `rstrip("/")`s
the catalog URL.

**The scheme.** An OCIRepository is always `oci://`, whatever the Catalog CR
says. A HelmRepository keeps the catalog's own scheme — the converter never
emits `spec.type: oci` for Path B (see ADR 0030), so an `oci://` HelmRepository
is not a thing this tool produces. A scheme in the option value is therefore
never data. It is either redundant or wrong, and `check_registry_override`
reports the wrong one as a `PreflightError`: `oci://` against a Helm-only
catalog, or an HTTP scheme against an OCI catalog. Accepting a contradicting
scheme silently would teach the operator that the flag sets a scheme, which it
does not.

**The rewrite.** A pure helper in `converter/` — `override_catalog_registry
(catalog, host) -> dict` — returns a copy of the Catalog CR with the host of
one URL replaced: the OCI URL when `catalog_has_oci(catalog)`, the Helm URL
otherwise. It writes back into the same place the selector read from, so the
first matching `spec.repositories[]` entry, or `spec.storage` when the
repositories list has no entry of that type. The other entries are left alone.
Rewriting every entry was considered and dropped: the converter reads exactly
one of them, and a Catalog CR whose unused entries were silently rewritten is a
worse thing to hand back than one that was touched where it was read.

`catalog_has_oci` moves from `converter/__init__.py` to
`converter/resources.py`, where the two URL selectors already live, and is
re-exported. `preflight`'s `from converter import catalog_has_oci` keeps
working. The alternative — the helper in `__init__.py` importing the two
private selectors — makes them public for one caller, or makes the import
circular.

**Where it runs.** `main.py` calls the helper between the Resolve section and
`convert()`, on `result.catalog`. `convert()` and the five `build_*` signatures
do not change: threading a `registry_host` parameter through all of them to
reach one line of use costs five signatures for nothing, and the converter is
already a pure function over a Catalog CR dict — so hand it the Catalog CR it
should have been given.

The helper returns a copy. The fetched object stays as it was, which keeps the
converter's inputs immutable and leaves `result.catalog` usable by anything
added later.

**What the operator sees.** One line at the top of the "Generated Flux
resources" section, above the YAML:

```
ℹ️  Registry host overridden to gsociprivate.azurecr.io.
```

It prints every time the flag is given, including when the host it names is
the host the Catalog CR already had. One rule, no branch, and the line still
confirms the value the run used. It is an info line, not a
`PreflightWarning`: the operator asked for this, and the full URL is in the
YAML immediately below.

No warning fires when `--override-registry-url` is used without
`--pull-secret`. A mirror can be anonymous, and the flag is just as useful for
one public registry standing in for another.

## Testing

The seams are the three that already exist. No new one.

- `tests/test_resources.py` — `override_catalog_registry` against a Catalog CR
  dict: OCI path, Helm path, `spec.storage` fallback, a port in the host, a
  catalog URL whose scheme is not `oci://` on an OCI entry (forced to `oci://`),
  the untouched sibling entries, and the input dict left unchanged. Same shape
  as the existing `_catalog(url=...)` helpers in that file.
- `tests/test_preflight.py` — `check_registry_override`: no flag, a bare host,
  a matching scheme, `oci://` against a Helm-only catalog, an HTTP scheme
  against an OCI catalog. Same shape as `check_pull_secret`'s cases.
- `tests/test_main.py` — the `migrate` run with the flag: the generated URL,
  the info line, and the `BadParameter` exit for a value with a path. Same
  `CliRunner` shape as the `--pull-secret` cases.

Every test works on dicts and the CLI. None of them reaches a cluster.

## Out of scope

- **Rewriting the path.** The mirror copies the registry, not the layout. A
  mirror that also moves the path needs a different flag, and a real case
  before one is designed.
- **Rewriting the chart name.** Same reason, and the App CR is the source of
  truth for it.
- **A scheme override.** Path A is `oci://` by definition, Path B keeps the
  catalog's scheme. There is nothing left for the operator to choose.
- **`spec.type: oci` on a HelmRepository.** Out of scope for ADR 0030 and
  still out of scope here.
- **Checking that the mirror is reachable, or that the chart is in it.** The
  tool has never talked to a registry. `MonitorHelmRelease` reports the
  failure if the mirror is wrong.
- **A per-catalog or config-file mapping of registries.** One flag for one run
  covers the known case.
- **The other commands.** `cleanup`, `suspend` and `resume` convert nothing.

## Consequences

- An installation that mirrors the Giant Swarm registry migrates without
  hand-editing the generated YAML.
- A contradicting scheme in the flag value fails in preflight, before any live
  mutation, and under `--dry-run` too.
- The operator cannot express a mirror that also moves the chart path. That is
  a deliberate limit, and the flag name says so.
- `catalog_has_oci` moves file, with its import in `preflight` unchanged.
- `convert()` and the `build_*` functions are untouched.
- Tracked as feature-parity backlog item 26 in `CLAUDE.md`.
