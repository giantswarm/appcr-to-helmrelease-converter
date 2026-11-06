# 34. Prevent-deletion label on Cluster chart resources

Date: 2026-11-06

## Status

Accepted

## Context

Deleting the resource that deploys a cluster deletes the cluster. Cluster chart
App CRs therefore carry the `giantswarm.io/prevent-deletion` label, and the
Kyverno ClusterPolicy `block-resource-deletion-if-has-prevent-deletion-label`
(giantswarm/kyverno-policies-ux) denies any DELETE of a labelled resource.
That policy now covers `HelmRelease` and `OCIRepository` too.

The label already reaches both generated resources when the App CR has it: the
Conversion copies App CR labels (ADR 0017, ADR 0025). An App CR without it
would silently produce an unprotected cluster.

## Decision

**Release chart substitution adds the label.** `substitute_release_chart()`
sets `giantswarm.io/prevent-deletion: "true"` on the App CR copy it hands to
the Conversion, unless the label is already present, in which case its value is
kept. The Kyverno selector matches any value. The label lands on the
OCIRepository and the HelmRelease through the ordinary label copy. Every
Cluster chart App goes through substitution (ADR 0032), and no other app does.

**The rollback removes the label before deleting.** `ApplyFluxResources`
deletes the resources it applied when the HelmRelease apply fails, or when a
later step fails and the run is reverted. Kyverno would deny those deletes.
The HelmRelease suspend merge-patch now also removes the label, and the source
gets the same label-removal merge-patch right before its delete. That patch is
best effort: if it fails, the delete reports the real error. This applies to
every app, because any App CR that carries the label passes it through.

## Consequences

- Converted Cluster chart apps are protected from the moment they are applied.
- Deleting a converted cluster takes the same deliberate step as before: remove
  the label first.
- For Flux-managed apps, the generated YAML committed to the gitops repository
  carries the label, so it stays after reconciliation.
