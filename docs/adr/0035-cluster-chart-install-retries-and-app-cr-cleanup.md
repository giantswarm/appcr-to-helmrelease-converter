# 35. No install retries for Cluster chart HelmReleases; `cleanup` clears the prevent-deletion label

Date: 2026-11-19

## Status

Accepted

## Context

ADR 0034 labels Cluster chart resources with `giantswarm.io/prevent-deletion`.
Two gaps remain.

Every generated HelmRelease has install remediation with `retries: 10`. When an
install fails, helm-controller remediates it by uninstalling the release before
the next attempt. For a Cluster chart app the release is the workload cluster,
so a failed install would delete it. `migrate` normally makes helm-controller
adopt the existing Helm release and upgrade it, but when it finds no release to adopt it
installs instead. kubectl-gs (giantswarm/kubectl-gs#2304) and gitops-template
(giantswarm/gitops-template#323) leave install retries out of cluster
HelmReleases for the same reason.

A Cluster chart App CR often carries `giantswarm.io/prevent-deletion` itself.
`cleanup` deletes the App CR after the migration, and the platform's Kyverno
policy refuses that `DELETE`.

## Decision

- For a Release chart app, `_build_helm_release_common()` in
  `converter/resources.py` emits install remediation with only
  `remediateLastFailure: false`, so a failed install isn't retried and its
  failure isn't remediated. The pure `is_release_chart_app(app)` in
  `converter/release_chart.py` tells it, from the substituted `spec.name`
  (ADR 0032). Other apps keep `retries: 10`.
- `cleanup` clears the label with a merge-patch (`null`) in the same patch that
  removes the finalizer, for the Chart CR and the App CR alike. Clearing an
  absent label is a no-op. The manual fallback printed by `migrate` clears it
  too.

## Consequences

- A failed install of a Cluster chart HelmRelease stays failed until someone
  looks at it, instead of uninstalling the cluster.
- `cleanup` deletes a labelled App CR. The HelmRelease and the OCIRepository
  keep their label (ADR 0034), so the cluster stays protected.
