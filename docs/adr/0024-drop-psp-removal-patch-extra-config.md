# 24. Always drop psp-removal-patch* extraConfigs entries

Date: 2026-07-14

## Status

Accepted

## Context

Some App CRs carry a `spec.extraConfigs[]` entry that patches away a chart's
`PodSecurityPolicy` rendering:

```yaml
spec:
  extraConfigs:
    - name: psp-removal-patch
      kind: configMap
      namespace: my-app-namespace
```

This is created by app-admission-controller's `mutateConfigForPSPRemoval`
(see [`mutate_app_psp_removal.go`](https://github.com/giantswarm/app-admission-controller/blob/v2.0.1/pkg/app/mutate_app_psp_removal.go#L92-L106)),
a temporary mutation working around [roadmap#2716](https://github.com/giantswarm/roadmap/issues/2716),
to be reverted once all managed apps are on Release >= v19.3.0. It became
necessary once PSPs were deprecated, and unnecessary the moment they were
removed entirely in Kubernetes 1.25 — the field it patches doesn't exist in
the cluster's API surface anymore, so carrying the reference forward into a
HelmRelease serves no purpose.

The name isn't always the literal string `psp-removal-patch`. AAC supports
per-app custom patches (configured in
[`configmap-values.yaml.template`](https://github.com/giantswarm/shared-configs/blob/2842b3b9399dbe7efb7b9247d6b36c51d636c8eb/default/apps/app-admission-controller/configmap-values.yaml.template#L7-L74)),
which use a suffixed name instead: `psp-removal-patch-<suffix>` (e.g.
`psp-removal-patch-datadog`, `psp-removal-patch-grafana-app`), truncated to 60
characters, always joined with a literal `-`. `Namespace` is always set to the
App CR's own namespace, and `Kind` is always `configMap`.

## Decision

`calculate_values_from` (`converter/values_from.py`) unconditionally excludes
any `spec.extraConfigs[]` entry where:

- `kind` canonicalizes to `ConfigMap` (case-insensitive, matching existing
  kind canonicalization), and
- `name` starts with `psp-removal-patch` (plain prefix match — AAC always
  joins the suffix with `-`, so this also matches the unsuffixed default), and
- the entry's `namespace` is the App CR's own namespace (`metadata.namespace`).
  If `namespace` is absent on the entry, it defaults to the App CR's own
  namespace (same default `fetcher/__init__.py` uses when walking
  `extraConfigs` for the Resolver), so it still matches.

The namespace check exists because AAC always sets `Namespace: app.Namespace`
on the entries it creates — matching that constraint means we only ever drop
entries that could plausibly be AAC's doing, not a same-prefixed name a user
coincidentally picked in a different namespace.

Scope is deliberately narrow: only `spec.extraConfigs[]` is checked, not
`spec.config.configMap` / `spec.userConfig.configMap`. AAC only ever writes
through `extraConfigs`.

The existing `check_psp_removal_patch` preflight check is updated to use the
same prefix + namespace predicate, so the operator sees a warning whenever any
matching entry (default or suffixed) is present:

```
warning: extraConfigs contains "psp-removal-patch-datadog" (ConfigMap); this
legacy PodSecurityPolicy patch is always dropped — PodSecurityPolicies were
removed in Kubernetes 1.25. If the chart still errors without it, upgrade the
chart instead of restoring this entry.
```

Fetch and Resolve are untouched — matching entries are still fetched and (if
they have multiple keys) still go through the normal interactive `valuesKey`
flow. Filtering only happens at the point `valuesFrom` is built, in the
converter.

## Consequences

- Converted HelmReleases never carry a `valuesFrom` reference to any
  `psp-removal-patch*` ConfigMap in the App CR's own namespace, even though
  the source App CR does.
- This is a one-way, hardcoded exclusion — there is no flag to keep it. If a
  chart genuinely still needs it post-migration, that's a signal the chart
  needs a real fix, not a config passthrough.
- A user-defined extraConfigs entry that happens to start with
  `psp-removal-patch` in the App CR's own namespace would also be silently
  dropped. Considered and accepted as a false-positive risk: AAC owns this
  naming convention in practice, and the preflight warning still surfaces it.
