# 17. Filter Flux labels and annotations by prefix

Date: 2026-06-24

## Status

Accepted.

## Context

The App CR may carry labels and annotations stamped by Flux controllers — most
commonly `kustomize.toolkit.fluxcd.io/name` and
`kustomize.toolkit.fluxcd.io/namespace` when the App CR itself is managed by a
Kustomization. The converter passes App CR metadata through to the generated
HelmRelease (minus a small exact blocklist), so these Flux-internal keys would
land on the HelmRelease. That is wrong: the HelmRelease is a new object managed
by a different Flux reconciler, and inheriting labels from the App CR's
reconciler would confuse Flux tooling.

## Decision

Any label or annotation key containing `fluxcd.io/` is dropped from the
generated HelmRelease. The check is a substring match so it covers all Flux
controller domains (`kustomize.toolkit.fluxcd.io`, `helm.toolkit.fluxcd.io`,
`source.toolkit.fluxcd.io`, etc.) without needing per-controller entries.

The following GiantSwarm-owned keys are dropped via an exact blocklist, as they
do not match the `fluxcd.io/` prefix:

- `app-operator.giantswarm.io/latest-configmap-version` (annotation)
- `app-operator.giantswarm.io/latest-secret-version` (annotation)
- `app-operator.giantswarm.io/paused` (annotation)
- `app-operator.giantswarm.io/version` (label)
- `chart-operator.giantswarm.io/force-helm-upgrade` (annotation)

## Considered options

- **Exact blocklist** — enumerate known Flux keys. Rejected: new Flux controller
  versions or additional labels would silently pass through until manually added.
- **Prefix filter on `toolkit.fluxcd.io`** — narrower than `fluxcd.io`. Rejected:
  no reason to pass through any key under the `fluxcd.io` domain; broader is safer.
