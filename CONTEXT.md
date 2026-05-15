# appcr-to-helmrelease-converter

A tool that converts Giant Swarm App CRs into the equivalent Flux CD resources for live cluster migrations.

## Language

**App CR**:
A Giant Swarm custom Kubernetes resource (`kind: App`) that declares an app to be deployed via the Giant Swarm app platform.
_Avoid_: App resource, app manifest

**HelmRelease**:
A Flux CD custom resource (`kind: HelmRelease`) that drives a Helm deployment. The primary output of a conversion.
_Avoid_: HR (in prose), helm release

**OCIRepository**:
A Flux CD custom resource (`kind: OCIRepository`) that points to a Helm chart in an OCI registry. Always produced alongside a HelmRelease as a pair.
_Avoid_: OCI source, chart source

**Conversion**:
The act of transforming one App CR into an OCIRepository + HelmRelease pair.
_Avoid_: migration (reserved for the broader live migration process), transformation

**valuesFrom**:
The list of config source references on a HelmRelease, ordered by ascending priority. Each entry points to a ConfigMap or Secret that supplies Helm values.
_Avoid_: values sources, config sources

**Priority**:
An integer that controls merge order in `valuesFrom`. Higher priority wins on key conflicts. The converter assigns 25 (extraConfigs default), 50 (spec.config), or 100 (spec.userConfig).
_Avoid_: weight, order

**extraConfigs**:
An App CR field (`spec.extraConfigs[]`) listing additional config sources beyond the standard config and userConfig slots.
_Avoid_: extra configs, additional configs

**valuesKey**:
The key inside a ConfigMap or Secret that holds Helm values. The converter emits `configmap-values.yaml` or `secret-values.yaml` — diverging from the App platform's `.data.values` — following the GiantSwarm Flux migration convention.
_Avoid_: data key, values field

**Catalog CR**:
A Giant Swarm custom resource (`kind: Catalog`) that describes a chart registry. `spec.repositories[].type` is `oci` or `helm`; `spec.repositories[].URL` is the registry URL. `spec.catalog` on an App CR is the name of a Catalog CR on the MC. Replaces the deprecated `AppCatalog` CR.
_Avoid_: AppCatalog, catalog resource

**GS catalog**:
A Catalog CR owned and operated by Giant Swarm, backed by the OCI registry at `gsoci.azurecr.io`. All charts are addressed as `oci://gsoci.azurecr.io/charts/giantswarm/{chart-name}` — the `giantswarm` path segment is a fixed constant, not the catalog name. GS catalogs are identified by the pair `(spec.catalog, resolved-namespace)` — a name alone is insufficient because a non-GS catalog could share the same name in a different namespace. GS catalogs are either `public` (Catalog CR in the `default` namespace) or `internal` (Catalog CR in the `giantswarm` namespace). When `spec.catalogNamespace` is absent on an App CR, the resolved namespace is `default`.

Known GS catalogs:

| name | type | namespace |
|---|---|---|
| cluster | public | default |
| cluster-test | internal | giantswarm |
| control-plane-catalog | internal | giantswarm |
| control-plane-test-catalog | internal | giantswarm |
| default | internal | giantswarm |
| default-test | internal | giantswarm |
| giantswarm | public | default |
| giantswarm-operations-platform | internal | giantswarm |
| giantswarm-operations-platform-test | internal | giantswarm |
| giantswarm-playground | internal | giantswarm |
| giantswarm-playground-test | internal | giantswarm |
| giantswarm-test | internal | giantswarm |
| releases | internal | giantswarm |
| releases-test | internal | giantswarm |

App CRs referencing a GS catalog convert to an OCIRepository + HelmRelease pair.
_Avoid_: internal catalog

**Non-GS catalog**:
A Catalog CR backed by a third-party HTTP Helm repository. The HTTP URL is not derivable from the App CR alone — it must be resolved by looking up the Catalog CR on the MC (`spec.repositories[type=helm].URL`). App CRs referencing a non-GS catalog convert to a HelmRepository + HelmRelease pair instead of an OCIRepository.
_Avoid_: external catalog, third-party catalog

**HelmRepository**:
A Flux CD custom resource (`kind: HelmRepository`) that points to an HTTP-based Helm chart registry. Produced instead of an OCIRepository when the App CR references a non-GS catalog.
_Avoid_: Helm repo source, chart repository resource

**app-operator**:
The Giant Swarm operator that watches App CRs and translates them into Chart CRs. Resolves Catalog CRs, handles `kubeConfig` routing to remote clusters, and fans out `extraConfigs`/`config`/`userConfig` into a flat Chart CR.
_Avoid_: app operator (no hyphen)

**chart-operator**:
The Giant Swarm operator that watches Chart CRs and drives `helm install`/`helm upgrade` against the target cluster. Deprecated — the live migration to Flux replaces it.
_Avoid_: chart operator (no hyphen)

**In-cluster app**:
An App CR with `spec.kubeConfig.inCluster: true`. The Helm release runs on the same cluster where the App CR lives (the MC). The converted HelmRelease needs no `spec.kubeConfig` field.
_Avoid_: local app

**Remote-cluster app**:
An App CR with `spec.kubeConfig.inCluster: false` and a `spec.kubeConfig.secret.name` pointing to a kubeconfig Secret. The Helm release targets a different cluster (typically a workload cluster). The converted HelmRelease must carry `spec.kubeConfig.secretRef.name` pointing to the same Secret.
_Avoid_: cross-cluster app, out-of-cluster app

**namespaceConfig**:
An App CR field (`spec.namespaceConfig`) that supplies annotations and labels to apply to the target namespace. Has no direct equivalent in a HelmRelease — requires a separate `Namespace` resource or is handled out-of-band.
_Avoid_: namespace metadata, namespace config

**Management Cluster (MC)**:
The Kubernetes cluster that runs app-operator and chart-operator, where App CRs live.
_Avoid_: management plane, control cluster

**Live migration**:
The broader operational process of suspending an App CR, applying the converted Flux resources, and monitoring the rollout. Distinct from a conversion, which is only the resource translation step.
_Avoid_: migration (ambiguous — always qualify as "live migration")

**Suspension**:
Pausing app-operator and chart-operator reconciliation on an App CR / Chart CR by setting the respective `paused` annotations, so Flux can take ownership without conflict.

## Relationships

- One **App CR** produces one **HelmRelease** plus either one **OCIRepository** (GS catalog) or one **HelmRepository** (non-GS catalog)
- A **HelmRelease** references its chart source via `spec.chartRef` (OCIRepository) or `spec.chart.spec.sourceRef` (HelmRepository)
- **valuesFrom** entries are derived from `spec.extraConfigs`, `spec.config`, and `spec.userConfig` on the **App CR**, merged in ascending **priority** order
- A **live migration** wraps a **conversion**: suspension → apply converted resources → monitor
- **app-operator** translates an **App CR** into a **Chart CR** by resolving the **Catalog CR**; **chart-operator** reconciles the **Chart CR** into a Helm release
- `spec.catalog` on an **App CR** is the name of a **Catalog CR** on the MC; GS catalogs have a known OCI URL pattern, non-GS catalogs require a live lookup of the **Catalog CR** to obtain the HTTP URL
- A **remote-cluster app** carries a kubeconfig Secret reference on both the **App CR** (`spec.kubeConfig.secret.name`) and the converted **HelmRelease** (`spec.kubeConfig.secretRef.name`)

## Example dialogue

> **Dev:** "Should we call it a migration or a conversion?"
> **Domain expert:** "Conversion is just the YAML translation. Migration is the full live operation — suspend, apply, monitor. Keep them separate."

> **Dev:** "Why does the valuesKey differ from what the App platform docs say?"
> **Domain expert:** "When you migrate, the ConfigMaps and Secrets get renamed following the GiantSwarm Flux convention. The new key names are `configmap-values.yaml` and `secret-values.yaml`, not `.data.values`."

## Scope decisions

- `spec.install`, `spec.upgrade`, `spec.rollback`, `spec.uninstall` on App CRs are always empty `{}` in observed real data. Decision: the converter omits them entirely; non-empty blocks are out of scope.
- `spec.config.configMap`, `spec.config.secret`, `spec.userConfig.configMap`, and `spec.userConfig.secret` sub-fields with an empty `name` are Go zero-value structs serialised to YAML — the App CR schema uses pointer-free structs so the field appears on the wire even when never populated. The converter skips these entries with a preflight warning, matching app-operator's behaviour. See ADR 0006.
- The `giantswarm` segment in `oci://gsoci.azurecr.io/charts/giantswarm/{chart}` is a fixed constant for all GS catalogs — it does not vary with `spec.catalog`.

## Flagged ambiguities

- "migration" was used loosely to mean both resource translation and the full live rollout — resolved: **conversion** = YAML translation only, **live migration** = full operational process.
