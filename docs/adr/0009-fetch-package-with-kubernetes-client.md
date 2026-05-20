# 9. Fetch package using the Python kubernetes client

Date: 2026-05-20

## Status

Accepted

## Context

`fetch-and-convert` (and the future `migrate` command) must fetch an App CR and
its Catalog CR from the MC. Two implementation options were considered:

1. **Shell out to `kubectl`** — `subprocess` call to `kubectl get ... -o yaml`.
   No new dependency; works with the user's existing kubeconfig. Downsides: fragile
   if `kubectl` is not on PATH; error handling requires parsing stderr strings;
   harder to test without a real shell environment.
2. **Python `kubernetes` client** — `pip install kubernetes`. Programmatic API,
   typed exceptions, native kubeconfig and context support. Heavier dependency but
   significantly cleaner code and easier to mock in tests.

Option 2 was chosen. `migrate` will also need programmatic cluster access (to apply
resources and watch rollout status), so the dependency cost is paid once and reused.

## Decision

A new `fetcher/` package is introduced. It is an I/O package (consistent with ADR
0001: I/O is separated from the pure `converter/` functions).

Public interface:

```python
def fetch(name: str, namespace: str, context: str | None = None) -> tuple[dict, dict]:
    ...
```

Returns `(app_dict, catalog_dict)`. Internally:

1. Loads kubeconfig from the default location (`~/.kube/config`) or the context
   specified by `context`.
2. Fetches the App CR by `name` and `namespace`.
3. Reads `spec.catalog` and `spec.catalogNamespace` from the App CR to derive the
   Catalog CR name and namespace (`default` when `spec.catalogNamespace` is absent).
4. Fetches the Catalog CR.
5. Returns both as plain dicts (same shape as `yaml.safe_load` output).

The `fetcher/` package has no knowledge of GS vs non-GS catalogs — it always
fetches both CRs unconditionally. Path selection is the converter's responsibility.

## Consequences

- New dependency: `kubernetes` Python client (pinned exact version in
  `requirements.txt`, consistent with project convention).
- `fetcher/` is explicitly an I/O package; `converter/` remains pure functions.
  The boundary from ADR 0001 is preserved.
- `fetcher.fetch()` is straightforward to mock in tests: return a tuple of two
  dicts, no subprocess or network required.
- `migrate` can reuse `fetcher/` directly when it is implemented.
- Users running `fetch-and-convert` need a valid kubeconfig with read access to
  App CRs and Catalog CRs on the MC. If the Catalog CR lives in a namespace the
  user cannot read, the fetch fails with a clear permission error from the
  kubernetes client.
