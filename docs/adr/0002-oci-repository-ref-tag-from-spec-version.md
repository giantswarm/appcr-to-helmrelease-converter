# 2. OCIRepository ref uses spec.version as an exact tag

Date: 2026-05-14

## Status

Accepted

## Context

When building an OCIRepository from an App CR, the `spec.ref` field must point
to a specific chart version. Two options were considered:

- `ref.semver`: a wildcard range (e.g. `x.x.x`) that lets Flux auto-update
  within a matching range.
- `ref.tag`: an exact OCI tag that pins the chart to a specific version.

The converter is a migration tool — its job is to produce Flux resources that
behave identically to the App CR being replaced, not to introduce new update
policies. App CRs always carry a specific `spec.version`. The `gsoci.azurecr.io`
registry stores chart tags without a `v` prefix (e.g. `1.2.3`, not `v1.2.3`),
which matches the bare semver format used in `spec.version`.

## Decision

`OCIRepository.spec.ref.tag` is set to the value of `app.spec.version` verbatim.
No semver validation is applied — the registry is the authority on which tags
exist. An empty `spec.version` is a hard error: the converter rejects the input
rather than falling back to a wildcard, because producing an OCIRepository with
an empty tag would silently yield a broken resource.

Post-migration upgrade policy (e.g. Flux image automation, manual tag bumps) is
out of scope for this tool.

## Consequences

- Converted OCIRepositories are pinned to the exact version that was running
  before migration — no unintended updates on first Flux reconciliation.
- Operators must update `spec.ref.tag` on the OCIRepository to upgrade a chart
  after migration.
- App CRs with an empty `spec.version` cannot be converted and surface a clear
  error.
