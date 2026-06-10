# 16. Resolver layer with interactive valuesKey selection

Date: 2026-06-10

## Status

Accepted.

## Context

A HelmRelease `valuesFrom` entry carries a `valuesKey` field that names the data
key inside the referenced ConfigMap or Secret that holds Helm values. The converter
previously hardcoded `configmap-values.yaml` (ConfigMaps) and `secret-values.yaml`
(Secrets) following the GiantSwarm Flux migration convention.

This is wrong when the actual resource uses a different key. The Flux default
`valuesKey` is `values.yaml` — if the field is omitted, Flux reads that key.
Emitting an explicit `valuesKey` that does not match the real data key causes the
HelmRelease to silently receive no values after migration.

The correct key can only be determined by inspecting the live resource. The app
platform guarantees that every ConfigMap and Secret referenced by a running App CR
exists on the Management Cluster.

## Decision

### Resolver step

A new `resolver/` package runs after preflight and before conversion in the
`migrate` command:

```
fetch → preflight → resolve → convert → confirm → suspend → apply → monitor
```

The resolver:

1. Iterates every ConfigMap and Secret referenced in `spec.extraConfigs`,
   `spec.config`, and `spec.userConfig` on the App CR.
2. Looks each one up in `app.metadata.namespace` on the MC via `CoreV1Api`
   (ConfigMaps) or `CoreV1Api` (Secrets). Missing resource → **preflight error**
   (the app platform guarantees existence for a running app).
3. If the resource has **one data key** → use it.
4. If the resource has **multiple data keys** → prompt the user to choose.
5. If the resolved key equals `values.yaml` (the Flux default) → store `None` in
   the override (converter omits `valuesKey`). Otherwise → store the key string.

### Resolution dataclass

```python
@dataclass
class Resolution:
    key_overrides: dict[tuple[str, str], str | None]
    # (kind, name) → resolved key, or None to omit valuesKey (Flux default)
```

`Resolution` is the extension point for future interactive resolver decisions.

### Converter interface

`converter.convert()` and `calculate_values_from()` gain an optional
`resolution: Resolution | None = None` parameter. When `None`, the converter falls
back to hardcoded defaults (`configmap-values.yaml` / `secret-values.yaml`), which
keeps the test suite usable without a resolver.

### Cross-namespace valuesFrom preflight check

A new pure-dict preflight check (added to `preflight/`) errors if any valuesFrom
ref carries a `namespace` that differs from `app.metadata.namespace`. Flux does not
support cross-namespace references in `valuesFrom`; the App CR namespace is always
the co-location requirement.

## Consequences

- `valuesKey` is always derived from real cluster data; hardcoded convention values
  are only a fallback in tests.
- The resolver is an explicit extension point: future interactive decisions (e.g.
  confirming target namespace, selecting between multiple catalog entries) add a
  field to `Resolution` and a step to the resolver without touching the converter.
- Offline conversion is not supported (see ADR 0015).
- The `migrate` flow gains one interactive step; users with single-key resources
  see no prompt.
