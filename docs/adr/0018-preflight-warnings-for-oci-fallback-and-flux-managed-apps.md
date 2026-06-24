# 18. Preflight warnings for OCI fallback and Flux-managed App CRs

Date: 2026-06-24

## Status

Accepted

## Context

Two cases during `migrate` require the operator to take action beyond running
the tool, but do not block conversion. Recording them as preflight warnings
(not errors) lets migration proceed while ensuring operators are explicitly
informed.

### OCI fallback

When a Catalog CR has no `type: oci` entry in `spec.repositories` (and no
`spec.storage.type: oci`), the converter falls back to a HelmRepository source.
This is silent today. A warning makes the path selection visible and surfaces the
relevant context: Flux has deprecated HelmRepository (no new features planned);
OCI is the preferred source type. Operators should be aware they are getting a
second-class source type so they can plan a future migration.

### Flux-managed App CR

When an App CR carries both `kustomize.toolkit.fluxcd.io/name` and
`kustomize.toolkit.fluxcd.io/namespace` labels, it is managed by a Flux
Kustomization. After migration the converted HelmRelease and source resource
(OCIRepository or HelmRepository) must be committed to the gitops repository
and the App CR removed from it — otherwise Flux will recreate the App CR and
undo the migration.

Cleanup also requires removing finalizers manually:

- `operatorkit.giantswarm.io/app-operator-app` from the App CR
- `operatorkit.giantswarm.io/chart-operator-chart` from the Chart CR

## Decision

Both checks are `PreflightWarning` (non-blocking). Migration continues because
the operator has already confirmed intent to migrate, and the manual steps are
post-migration concerns, not blockers to starting.

Two new check functions are added to `preflight/__init__.py` and registered in
`_CHECKS`:

- `check_oci_fallback` — warns when no `type: oci` is found in catalog
  repositories or storage; message explains HelmRepository is deprecated by Flux.
- `check_flux_managed` — warns when both kustomize name+namespace labels are
  present on the App CR; message names the exact finalizers to remove.
