# 6. valuesFrom entries with an empty name are skipped with a preflight warning

Date: 2026-05-14

## Status

Accepted

## Context

App CRs in the wild carry `.spec.config.configMap`, `.spec.config.secret`,
`.spec.userConfig.configMap`, and `.spec.userConfig.secret` sub-fields whose
`name` is the empty string:

```yaml
spec:
  config:
    configMap:
      name: my-config
      namespace: org-example
    secret:
      name: ""
      namespace: ""
```

These are Go zero-value structs serialised to YAML — the App CR schema uses
pointer-free structs so the field is always present on the wire even when the
operator never populated it. The GS App Platform (app-operator) treats a
reference with an empty `name` as absent and does not pass it to chart-operator.

The current converter's `to_reference_with_priority` guards on `if reference:`
(truthy dict check), which passes for `{"name": "", "namespace": ""}`. It then
emits `name: ""` into the HelmRelease `valuesFrom` list. Flux rejects a
HelmRelease with an empty `name` in `valuesFrom`, so the converted resource is
immediately invalid.

## Decision

### Conversion behaviour

An entry under `.spec.config`, `.spec.userConfig`, or `.spec.extraConfigs` with
an empty or absent `name` is silently dropped from `valuesFrom`, matching
app-operator's behaviour.

### Preflight warning

Before dropping, the converter surfaces a `PreflightWarning` for each affected
field path, so the operator is aware that a reference was encountered and
intentionally not carried over:

```
warning: spec.config.secret.name is empty; skipping valuesFrom entry
warning: spec.userConfig.configMap.name is empty; skipping valuesFrom entry
```

Conversion continues and exits zero. The message includes the full field path
(`spec.config.secret`, `spec.userConfig.configMap`, etc.) so the operator can
locate the source field without reading the raw YAML.

### Implementation

- `check_empty_values_from_names(app: dict) -> list` is added to `preflight/`
  and registered in `_CHECKS`. It inspects `.spec.config.configMap`,
  `.spec.config.secret`, `.spec.userConfig.configMap`, and
  `.spec.userConfig.secret`; for each one whose `name` is empty or absent it
  appends a `PreflightWarning` to a list and returns the list (possibly empty).
  This differs from other checks that raise a single exception: multiple fields
  can independently be empty, and each deserves its own warning.
- `run_preflight` is updated to call `issues.extend(result)` for checks that
  return a list, alongside the existing `try/except` path for checks that raise.
  Existing checks are unaffected.
- In `converter/values_from.py`, `to_reference_with_priority` adds an early
  guard: if `reference.get("name")` is falsy, return `None`. This is the
  correctness fence — it ensures no empty-name entry reaches the HelmRelease
  regardless of whether preflight was called.

The two changes are independent: the preflight check warns, the converter
guards. Both are needed — the preflight check is the visible signal, the
converter guard is the correctness fence.

## Consequences

- Converted HelmReleases never contain a `valuesFrom` entry with an empty name.
- Operators see a per-field warning for every zero-value reference encountered,
  rather than a silent drop or a hard error.
- The behaviour matches what app-operator does today, so the converted resource
  is functionally equivalent to the original App CR.
