# 24. Always drop the psp-removal-patch extraConfigs entry

Date: 2026-07-14

## Status

Accepted

## Context

Some App CRs carry a `spec.extraConfigs[]` entry named `psp-removal-patch`:

```yaml
spec:
  extraConfigs:
    - name: psp-removal-patch
      kind: configMap
      namespace: my-app-namespace
```

This is a ConfigMap that GS operators historically added to patch chart values
so a chart would stop rendering a `PodSecurityPolicy` resource. It became
necessary once PSPs were deprecated, and unnecessary the moment they were
removed entirely in Kubernetes 1.25 — at that point the field it patches
doesn't exist in the cluster's API surface anymore, so carrying the reference
forward into a HelmRelease serves no purpose.

## Decision

`calculate_values_from` (`converter/values_from.py`) unconditionally excludes
any `spec.extraConfigs[]` entry with `kind: ConfigMap` (case-insensitive,
matching existing kind canonicalization) and `name: psp-removal-patch`. The
match ignores `namespace` — in practice it's always the App CR's own
namespace, and namespace is already dropped from every `valuesFrom` entry.

Scope is deliberately narrow: only `spec.extraConfigs[]` is checked, not
`spec.config.configMap` / `spec.userConfig.configMap`. `psp-removal-patch` is
only ever introduced as an extra config in observed data; matching the other
two fields would be defensive code for a case that doesn't occur.

A new `check_psp_removal_patch` preflight check warns when the entry is
present, so the operator isn't surprised by output that's missing something
their original App CR referenced:

```
warning: extraConfigs contains "psp-removal-patch" (ConfigMap); this legacy
PodSecurityPolicy patch is always dropped — PodSecurityPolicies were removed
in Kubernetes 1.25. If the chart still errors without it, upgrade the chart
instead of restoring this entry.
```

Fetch and Resolve are untouched — the ConfigMap is still fetched and (if it
has multiple keys) still goes through the normal interactive `valuesKey` flow.
Filtering only happens at the point `valuesFrom` is built, in the converter.

## Consequences

- Converted HelmReleases never carry a `valuesFrom` reference to
  `psp-removal-patch`, even though the source App CR does.
- This is a one-way, hardcoded exclusion — there is no flag to keep the entry.
  If a chart genuinely still needs it post-migration, that's a signal the
  chart needs a real fix, not a config passthrough.
