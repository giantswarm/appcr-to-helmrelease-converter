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
A Flux CD custom resource (`kind: OCIRepository`) that points to a Helm chart in an OCI registry. Always produced alongside a HelmRelease as a pair. Carries the same filtered App CR labels and annotations as the HelmRelease (see ADR 0017, extended to source resources by ADR 0025).
_Avoid_: OCI source, chart source

**Conversion**:
The act of transforming an App CR dict and a Catalog CR dict into a Flux resource pair. Always receives two inputs: `(app_dict, catalog_dict)`. Produces an OCIRepository + HelmRelease pair (Path A) when the Catalog CR has an `oci` repository type, or a HelmRepository + HelmRelease pair (Path B) when only `helm` is present. Path selection reads `spec.repositories` first, falling back to the deprecated `spec.storage`.
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
A Giant Swarm custom resource (`kind: Catalog`) that describes a chart registry. `spec.catalog` on an App CR is the name of a Catalog CR on the MC. Replaces the deprecated `AppCatalog` CR.

Repository type and URL are read using this lookup order:
1. `spec.repositories[]` (current field) — array of `{type, URL}` entries. `oci` is preferred over `helm` if both are present.
2. `spec.storage` (deprecated) — single `{type, URL}` object; same shape as one `spec.repositories` entry. Used as fallback when `spec.repositories` is absent or null.

Hard error if neither field yields a recognised type (`oci` or `helm`). If multiple `helm` entries are present, the first URL is used and a preflight warning is emitted.
_Avoid_: AppCatalog, catalog resource

**HelmRepository**:
A Flux CD custom resource (`kind: HelmRepository`) that points to an HTTP-based Helm chart registry. Produced when `catalog_dict["spec"]["repositories"][].type` is `helm`. Carries the same filtered App CR labels and annotations as the HelmRelease (see ADR 0017, extended to source resources by ADR 0025).
_Avoid_: Helm repo source, chart repository resource

**Fetch**:
The act of pulling an App CR and its Catalog CR from the MC by name and namespace, using the Kubernetes API. The Catalog CR name is taken from `spec.catalog`. The namespace is resolved as follows: if `spec.catalogNamespace` is set, only that namespace is checked; otherwise `default` is tried first, then `giantswarm`, using the first namespace where the Catalog CR is found (mirroring app-operator behaviour). A non-404 error during the fallback fails immediately. Hard error if the catalog is not found in any checked namespace. Always produces two dicts: the App CR and the Catalog CR. Implemented in the `fetcher/` package.
_Avoid_: lookup, resolve, cluster fetch

**Resolution**:
The output of the Resolver step — a dataclass carrying decisions made through interactive cluster lookup that inform the Conversion. Today it carries `key_overrides`: a mapping of `(kind, name)` pairs to the resolved `valuesKey` string (or the Flux default `values.yaml` when the field should be omitted). Designed as an extension point: future interactive decisions add fields here without changing the Conversion interface.
_Avoid_: resolved config, lookup result

**Resolver**:
The step between preflight and Conversion that looks up every ConfigMap and Secret referenced in the App CR's `valuesFrom` sources from the MC, determines the correct `valuesKey` for each, and prompts the user when a resource has multiple data keys. Produces a [[Resolution]]. A missing ConfigMap or Secret is a hard error (the app platform guarantees these exist for any running app). Runs only in the `migrate` command — offline conversion is not supported.
_Avoid_: lookup step, key resolver

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

**Chart CR**:
A Giant Swarm custom resource (`kind: Chart`) created by app-operator for each App CR. Watched by chart-operator, which drives the actual Helm install/upgrade. Always lives in the `giantswarm` namespace on the cluster where chart-operator runs: the MC for in-cluster apps, the WC for remote-cluster apps.

Name is derived from the App CR name by stripping the `cluster_id` prefix and suffix (both applied in sequence) using the `giantswarm.io/cluster` label on the App CR — but only for a remote-cluster app in a dedicated per-app namespace:
```
chart_name = app_name.removeprefix(f"{cluster_id}-")
chart_name = chart_name.removesuffix(f"-{cluster_id}")
```
The `giantswarm.io/cluster` label alone is not a reliable predicate for stripping: it also marks in-cluster, per-workload-cluster component apps that share one namespace on the MC (e.g. `operations-auth-bundle` alongside `operations-app-operator` in one org namespace), and those do **not** strip the prefix — their Chart CR name equals the App CR name unstripped. The real predicate is whether the app is remote (`spec.kubeConfig.inCluster` false), not label presence. See ADR 0028.
_Avoid_: chart resource, helm chart CR

**Suspension**:
Pausing app-operator and chart-operator reconciliation on an App CR / Chart CR by setting the respective `paused` annotations, so Flux can take ownership without conflict.

**Resume**:
The inverse of Suspension: un-pausing app-operator and chart-operator reconciliation on an App CR / Chart CR by clearing the respective `paused` annotations. Idempotent — clearing an annotation that's already absent is a no-op.
_Avoid_: unpause (implementation detail — the annotation-clearing mechanics, not the domain concept), un-suspend

**Deployed App CR**:
An **App CR** whose Helm release is settled and healthy: app-operator reports its release status as `deployed`, and the version it reports as deployed equals the version the **App CR** asks for. Only a deployed App CR is a valid starting point for a **live migration**.
_Avoid_: ready App CR (App CRs carry no Ready condition), healthy app

**Release CR**:
A Giant Swarm cluster-scoped custom resource (`kind: Release`, group `release.giantswarm.io`) on the MC that pins the set of component and app versions making up one workload-cluster release for one provider (e.g. `aws-34.0.0`). Among its components is the **Cluster chart** version for that provider.
_Avoid_: release (unqualified), GS release

**Cluster chart**:
The original per-provider chart `cluster-<provider>` (e.g. `cluster-aws:7.2.5`) that defines a workload cluster. Its version is the chart's own version, not the **Release CR** version.
_Avoid_: cluster app, provider chart

**Release chart**:
The retagged `release-<provider>` chart (e.g. `release-aws:34.0.0`): the **Cluster chart** pinned by a **Release CR**, renamed, with its version set to the **Release CR** version. The only form of a cluster definition that a **Conversion** emits.
_Avoid_: release (unqualified), retagged chart

**Installation values**:
The installation-wide `cluster-app-installation-values` ConfigMap that **Cluster chart** apps take values from. Its source lives in the `giantswarm` namespace; the platform keeps a copy in every `org-*` namespace for HelmReleases, which can only read values from their own namespace. A **Conversion** of a **Cluster chart** app reads the copy.
_Avoid_: installation ConfigMap, cluster values

**Release chart substitution**:
The **Conversion** special case that, for an **App CR** whose chart is a **Cluster chart**, emits the **Release chart** in its place. Only the chart name and version change; every other part of the converted resources stays identical to an ordinary **Conversion**, so Flux adopts the existing Helm release.
_Avoid_: chart swap, cluster special case

## Relationships

- One **App CR** + one **Catalog CR** produce one **HelmRelease** plus either one **OCIRepository** (`type: oci`) or one **HelmRepository** (`type: helm`)
- A **HelmRelease** references its chart source via `spec.chartRef` (OCIRepository) or `spec.chart.spec.sourceRef` (HelmRepository)
- **valuesFrom** entries are derived from `spec.extraConfigs`, `spec.config`, and `spec.userConfig` on the **App CR**, merged in ascending **priority** order
- A **live migration** wraps a **conversion**: suspension → apply converted resources → monitor
- **app-operator** translates an **App CR** into a **Chart CR** by resolving the **Catalog CR**; **chart-operator** reconciles the **Chart CR** into a Helm release
- `spec.catalog` on an **App CR** is the name of a **Catalog CR** on the MC; the **Catalog CR**'s `spec.repositories[].type` (`oci` or `helm`) determines which Flux source resource the **Conversion** produces
- A **Fetch** pulls an **App CR** and its **Catalog CR** from the MC and hands both dicts to the **Resolver** and then the **Conversion**
- The **Resolver** inspects the live ConfigMaps and Secrets referenced by the **App CR** and produces a **Resolution** that drives `valuesKey` decisions in the **Conversion**
- A **Release CR** pins exactly one **Cluster chart** version; one **Release chart** exists per published **Release CR**, and several **Release CRs** may pin the same **Cluster chart** version
- A **Release chart substitution** applies to every **App CR** whose chart is a **Cluster chart**; the workload cluster's Cluster CR names the **Release CR**, which must pin the **App CR**'s own chart version
- A **remote-cluster app** carries a kubeconfig Secret reference on both the **App CR** (`spec.kubeConfig.secret.name`) and the converted **HelmRelease** (`spec.kubeConfig.secretRef.name`)

## Example dialogue

> **Dev:** "Should we call it a migration or a conversion?"
> **Domain expert:** "Conversion is just the YAML translation. Migration is the full live operation — suspend, apply, monitor. Keep them separate."

> **Dev:** "How does the tool know which key inside a ConfigMap holds the Helm values?"
> **Domain expert:** "The Resolver looks up the live ConfigMap from the cluster. If there's one key, it uses that. If there are multiple, it asks the user. If the key is `values.yaml` (the Flux default), it omits the `valuesKey` field entirely. Hardcoded convention names like `configmap-values.yaml` are only a test-time fallback."

## Scope decisions

- `spec.install`, `spec.upgrade`, `spec.rollback`, `spec.uninstall` on App CRs are always empty `{}` in observed real data. Decision: the converter omits them entirely; non-empty blocks are out of scope.
- `spec.config.configMap`, `spec.config.secret`, `spec.userConfig.configMap`, and `spec.userConfig.secret` sub-fields with an empty `name` are Go zero-value structs serialised to YAML — the App CR schema uses pointer-free structs so the field appears on the wire even when never populated. The converter skips these entries with a preflight warning, matching app-operator's behaviour. See ADR 0006.
- A `spec.extraConfigs[]` entry whose name starts with `psp-removal-patch` (kind ConfigMap, in the App CR's own namespace) is a legacy artifact created by app-admission-controller to patch away `PodSecurityPolicy` rendering before PSPs were removed in Kubernetes 1.25 — the suffixed form (e.g. `psp-removal-patch-datadog`) comes from AAC's per-app custom patches. The converter always drops matching entries from `valuesFrom` with a preflight warning; there is no flag to keep it. See ADR 0024.


## Flagged ambiguities

- "migration" was used loosely to mean both resource translation and the full live rollout — resolved: **conversion** = YAML translation only, **live migration** = full operational process.
- "release" alone could mean a Helm release, a **Release CR**, or a **Release chart** — resolved: always qualify it; "Helm release" for the deployed Helm object.
