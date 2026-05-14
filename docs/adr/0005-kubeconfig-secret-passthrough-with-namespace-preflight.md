# 5. kubeConfig secret name is passed through; namespace mismatch is a preflight error

Date: 2026-05-14

## Status

Accepted

## Context

App CRs may carry `spec.kubeConfig` to target a remote cluster. The field has
three relevant sub-fields:

```yaml
spec:
  kubeConfig:
    inCluster: false          # bool; Go zero-value is false
    secret:
      name: example-kubeconfig
      namespace: org-giantswarm
```

app-operator's admission controller mutates every non-inCluster App CR before
it is persisted: it auto-populates `secret.name` and `secret.namespace` if they
are absent. This means any App CR that reaches the converter with
`inCluster: false` will always have both sub-fields set.

Flux's HelmRelease supports the same targeting pattern via
`spec.kubeConfig.secretRef.name`. It does **not** accept a `namespace` on the
reference — Flux requires the Secret to live in the same namespace as the
HelmRelease. The `secretRef.key` field is optional and defaults to `value`,
which is the same key used in CAPI-created kubeconfig Secrets (e.g.
`example-kubeconfig`). No Secret reformatting is needed.

## Decision

### Conversion behaviour

| App CR `spec.kubeConfig` | HelmRelease `spec.kubeConfig` |
|---|---|
| absent | absent (in-cluster assumed) |
| `inCluster: true` | absent |
| `inCluster: false` (or `kubeConfig` present without `inCluster`) | `secretRef.name: <secret.name>` |

`inCluster` is a Go `bool` with no `omitempty`, so its zero-value is `false`.
An App CR with `spec.kubeConfig` present but no explicit `inCluster` field is
therefore treated as a remote-cluster app, consistent with app-operator.

The `secret.namespace` field is dropped — carrying it over would produce
invalid YAML, as Flux does not support cross-namespace Secret references for
kubeConfig.

Since the HelmRelease `secretRef.key` defaults to `value` and CAPI kubeconfig
Secrets already use that key, `secretRef.key` is not emitted.

### Preflight check

If `spec.kubeConfig.secret.namespace` differs from `metadata.namespace`, the
converter halts with a preflight error:

```
error: spec.kubeConfig.secret.namespace "other-ns" does not match app namespace
"org-giantswarm"; Flux requires the kubeconfig Secret to be in the same
namespace as the HelmRelease
```

A mismatch means the converted HelmRelease would reference a Secret that Flux
cannot reach — silently dropping the namespace would produce a broken resource.
Halting is the only safe choice.

### Implementation plan

- Add `check_kube_config(app)` to `preflight/` and register it in `_CHECKS`.
  Raises `PreflightError` when `inCluster` is not `True`, `kubeConfig` is
  present, and `secret.namespace` differs from `metadata.namespace`.
- Update `build_helm_release` in `converter/resources.py`: read
  `app["spec"].get("kubeConfig", {})`. If `inCluster` is not `True` and the
  block is non-empty, insert `spec.kubeConfig.secretRef.name` into the
  HelmRelease spec.

## Consequences

- Remote-cluster App CRs convert correctly when the kubeconfig Secret is
  co-located with the App CR — the common case for CAPI-managed clusters.
- Cross-namespace kubeconfig references surface as a hard preflight error before
  any output is produced, preventing silently broken HelmRelease resources.
- No Secret reformatting is needed — CAPI kubeconfig Secrets already use the
  `value` key that Flux expects by default.
