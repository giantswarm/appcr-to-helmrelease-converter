# 34. Stamp `giantswarm.io/prevent-deletion` on Cluster chart resources

Date: 2026-11-10

## Status

Accepted

## Context

Deleting a non-suspended HelmRelease makes helm-controller run `helm
uninstall`. For a Cluster chart app (ADR 0032) the release *is* the workload
cluster, so an accidental delete — a `prune: true` Kustomization pruning it
when the gitops PR is reverted, a hand-run `kubectl delete`, a namespace
clean-up — tears down a whole cluster. Deleting its OCIRepository breaks every
later reconcile of that release.

Giant Swarm management clusters already enforce a Kyverno policy that refuses
`DELETE` on any object labelled `giantswarm.io/prevent-deletion: "true"`.

## Decision

When Release chart substitution applies — the App CR is a Cluster chart app —
the converter adds `giantswarm.io/prevent-deletion: "true"` to the labels of
the HelmRelease and its OCIRepository. (A Cluster chart app always takes the
OCI path: preflight rejects a catalog without OCI for it.)

It follows the `substitute_release_chart` pattern: the pure
`protect_from_deletion(app) -> dict` in `converter/release_chart.py` (constant
`PREVENT_DELETION_LABEL`) returns a copy of the App CR with the label set,
overriding any value the App CR carries for the key. `main.py` calls it right
after `substitute_release_chart()`, and the label reaches both generated
resources through the existing `_filtered_metadata()` copy. `convert()` and the
`build_*` signatures are unchanged; ordinary apps get no label.

The converter's own deletes clear the label first, with a merge-patch setting
it to `null`, for every app — clearing an absent label is a no-op:

- `ApplyFluxResources.revert()` clears it on the HelmRelease in the same
  merge-patch that sets `spec.suspend: true` (one call, so suspension still
  precedes the delete), and on the source just before deleting it, once the
  HelmRelease is confirmed gone. A failure to clear it is recorded as its own
  error; the delete is still attempted.
- `ApplyFluxResources.apply()`'s best-effort source clean-up after a failed
  HelmRelease apply does the same, swallowing errors as before.

`cleanup`, `suspend` and `resume` touch only App and Chart CRs and are
unchanged.

## Alternatives considered

- **Every generated resource.** Rejected: the uninstall risk is the same for
  every app, but only a Cluster chart's uninstall is unrecoverable at fleet
  scale, and a label on every app would make every deliberate removal a
  two-step operation.

## Consequences

- Because the label is in the generated YAML, it reaches gitops with the rest
  of the resource and survives Flux adoption.
- Reverting the gitops PR of a Cluster chart app no longer uninstalls the
  cluster: the Kustomization's prune is refused. A deliberate rollback must
  first suspend the HelmRelease and then remove the label (`kubectl label
  <kind> <name> giantswarm.io/prevent-deletion-`), exactly as the converter's
  own revert does.
- On a cluster without the Kyverno policy the label is inert.
