# 32. Release chart substitution for Cluster chart apps

Date: 2026-10-01

## Status

Accepted

## Context

A workload cluster is deployed from a **Cluster chart**, `cluster-<provider>`
(e.g. `cluster-aws:7.2.5`). Under the app platform its version is not chosen
directly. The operator sets `global.release.version` in the user ConfigMap,
app-admission-controller's mutating webhook looks up the matching **Release
CR** (`aws-34.0.0`) and rewrites the App CR's `spec.version` to the Cluster
chart version that Release CR pins.

Converting such an App CR the ordinary way would produce a HelmRelease that
deploys today, because the webhook already wrote the right `spec.version`. It
breaks on the first upgrade: nothing in Flux bumps the chart version when
`global.release.version` changes, so the operator has to remember to change two
values that must agree. That hidden coupling is why KaaS changed the
versioning, on request, so the deployed version is set explicitly
(giantswarm/giantswarm#33311, giantswarm/roadmap#4184).

The result is the **Release chart**: CI in giantswarm/releases retags each
active Release CR's Cluster chart as `release-<provider>:<release-version>`
(e.g. `release-aws:34.0.0`) with `global.release.version` baked into its
values, and pushes it to `oci://gsoci.azurecr.io/charts/giantswarm/` — the same
OCI URL the `cluster` Catalog CR already names. See
`docs/workflows-retagging-cluster-charts.md` in giantswarm/releases.

## Decision

When `migrate` converts an App CR whose chart is a Cluster chart, the
Conversion emits the Release chart in its place — **Release chart
substitution**. It is not optional: there is no flag to convert the Cluster
chart as-is.

**What qualifies.** The App CR's `spec.name` is `cluster-<provider>` for a
provider in a hardcoded list mirroring `SupportedProviders` in the releases SDK
(`sdk/api/v1alpha1/types.go`): `aws`, `azure`, `vsphere`, `cloud-director`,
`eks`, `proxmox`, `aks`. A bare `cluster-` prefix match is wrong —
`cluster-autoscaler` is not a Cluster chart. `metadata.name` plays no part.

**Finding the Release version.** From the CAPI Cluster CR the Helm release
owns: in the App CR's `spec.namespace`, the single `cluster.x-k8s.io` Cluster
labelled `app.kubernetes.io/instance=<Helm release name>`, read for its
`release.giantswarm.io/version` label. The chart writes that label straight
from `global.release.version`, so it describes what was last deployed. Zero or
several matching Clusters, or a missing label, is a hard preflight error. The
Cluster list tries `cluster.x-k8s.io/v1beta2` first and falls back to `v1beta1`
when the newer version is not served.

**Consistency.** The Release CR `<provider>-<version>` must exist, and the
Cluster chart version it pins in `spec.components[]` must equal the App CR's
`spec.version`. Any disagreement is a hard preflight error with no override:
it marks a special case — a failed upgrade, a hand-edited version — for an
operator, and nothing is translated forcibly.

**The Release chart must be published.** Preflight checks that
`release-<provider>:<version>` exists in public `gsoci.azurecr.io`, always,
regardless of `--override-registry-url`. The question is whether a Release
chart exists for this Cluster chart version, and only the upstream registry
answers it. Whether a mirror has synced it is the same open question as for
any other app on a mirrored installation, and gets no special treatment. A
check that cannot complete is a hard error. This is the first time the tool
talks to a registry; it replaces any release-version cutoff. The lookup is a
`HEAD` on the manifest through the OCI distribution API with an anonymous
bearer token, using the standard library and the same `certifi` CA bundle the
kubernetes client verifies against.

**What changes in the output.** Only the chart name and version: the
OCIRepository URL ends in `release-<provider>` and its `ref.tag` is the Release
version. Every other field — resource names, `releaseName`, namespaces,
`valuesFrom`, `kubeConfig` — is exactly what an ordinary Conversion emits.
Anything more risks Flux not adopting the existing Helm release, which for a
workload cluster is a disaster. `--override-registry-url` still applies to the
URL as for any app.

**Installation values.** Cluster chart App CRs take the installation-wide
`cluster-app-installation-values` ConfigMap from the `giantswarm` namespace
through `extraConfigs` (priority 10). Flux reads `valuesFrom` only from the
HelmRelease's own namespace, so that reference cannot be carried over as-is.
The platform already covers it on the Flux side: the Kyverno ClusterPolicy
`sync-cluster-app-configmap-to-org-namespaces` keeps a copy of the ConfigMap
in every `org-*` namespace, and the MutatingPolicy
`prepend-cluster-app-config-map-hr` prepends a same-namespace entry for it to
any `org-*` HelmRelease whose OCIRepository URL is a known Cluster chart or
Release chart. For a Cluster chart app the Conversion reads that entry from
the copy in the App CR's namespace and emits it explicitly —
`{kind: ConfigMap, name: cluster-app-installation-values}`, first in
`valuesFrom` by its priority — exactly the entry the policy would add, so the
policy has nothing to do. A missing copy is the ordinary missing-ConfigMap
preflight error. Preflight prints a notice when it happens.

**OCI only.** A Cluster chart App CR whose Catalog CR has no OCI repository is
a hard preflight error. Moving off plain Helm repositories is a goal in its own
right and should be done already; this is a fail-safe.

**What the operator sees.** A preflight notice, also under `--dry-run`:

```
Release chart substitution: cluster-aws@7.2.5 → release-aws@34.0.0 (Release CR aws-34.0.0)
```

**`global.release.version` in user values.** Left alone by `migrate`: during
the migration it equals the version baked into the Release chart, so it is
harmless. It must go before the first upgrade, because a values entry overrides
the chart's baked-in one and the chart would look up the old Release CR.
Removing it from the gitops repository is the operator's job, alongside
removing the App CR. `cleanup` verifies it is gone: no live ConfigMap or Secret
behind the HelmRelease's `valuesFrom` may still carry it. For a non-Flux-managed
app there is no repository to edit: `cleanup`, and `migrate`'s own clean-up for
such apps, offers to remove the key from the live resource, in the same `y/N`
prompt as the App CR and Chart CR deletion. The YAML under that key is
rewritten, so its comments are lost.

## Considered options

- **Reading the provider list from the releases SDK at runtime** — parsing Go
  source fetched from GitHub for a list that changes less than monthly.
- **Deriving providers from the Release CRs on the MC** — sound, but more
  machinery than a seven-item list warrants.
- **Reading `global.release.version` from merged values**, as the webhook does —
  needs a priority merge of every values source; the Cluster CR label is the
  same value, already rendered.
- **Finding the Release CR by its Cluster chart version** — ambiguous: several
  Release CRs pin the same Cluster chart version.
- **Checking the mirror instead of GSOCI** — answers reachability, not whether
  the Release chart exists.
- **Dropping the installation values entry and leaving it to the Kyverno
  policy** — the policy matches the OCIRepository URL against a fixed list, so
  `--override-registry-url` would silently lose the values; and with
  `failurePolicy: Ignore` a HelmRelease admitted while Kyverno is down
  reconciles once without them. Emitting the entry also makes the generated
  YAML say what Flux actually uses.

## Consequences

- `migrate` makes a new kind of external call: an anonymous OCI registry lookup
  against `gsoci.azurecr.io`. A laptop without that access cannot migrate a
  Cluster chart app.
- The HelmRelease reconciles as the org's `automation` ServiceAccount (ADR
  0014), not with chart-operator's rights. That is the intended identity:
  rbac-operator binds the `cluster-app-chart-lookups` ClusterRole to it in every
  org, granting read on Release CRs (the chart's own lookup) and the other
  cluster-scoped objects Cluster charts look up. Its notes say HelmReleases
  that install clusters should use that ServiceAccount. Inside the org
  namespace it holds `cluster-admin` through a RoleBinding, which covers every
  namespaced object the Helm release manages.
- Adding a provider means editing the hardcoded list.
- A cluster whose Release CR predates the retagging workflow, or is not
  `active`, has no Release chart and fails preflight — by design.
- `cleanup` gains a check, and for non-Flux-managed apps a prompt, scoped to
  Cluster chart apps.
