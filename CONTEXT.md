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

Hard error if neither field yields a recognised type (`oci` or `helm`).
_Avoid_: AppCatalog, catalog resource

**HelmRepository**:
A Flux CD custom resource (`kind: HelmRepository`) that points to an HTTP-based Helm chart registry. Produced when `catalog_dict["spec"]["repositories"][].type` is `helm`.
_Avoid_: Helm repo source, chart repository resource

**Fetch**:
The act of pulling an App CR and its Catalog CR from the MC by name and namespace, using the Kubernetes API. The Catalog CR name and namespace are derived from `spec.catalog` and `spec.catalogNamespace` on the App CR (`default` when `spec.catalogNamespace` is absent). Always produces two dicts: the App CR and the Catalog CR. Implemented in the `fetcher/` package.
_Avoid_: lookup, resolve, cluster fetch

**`fetch` command**:
The CLI command that runs a Fetch and emits the result as a two-document YAML stream (Catalog CR first, App CR second). Takes `--name`, `--namespace`, and optional `--context`. Output is raw and unmodified — server-side fields are preserved. Designed to be pipeable into `convert`.
_Avoid_: fetch command (without backticks in prose)

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

- One **App CR** + one **Catalog CR** produce one **HelmRelease** plus either one **OCIRepository** (`type: oci`) or one **HelmRepository** (`type: helm`)
- A **HelmRelease** references its chart source via `spec.chartRef` (OCIRepository) or `spec.chart.spec.sourceRef` (HelmRepository)
- **valuesFrom** entries are derived from `spec.extraConfigs`, `spec.config`, and `spec.userConfig` on the **App CR**, merged in ascending **priority** order
- A **live migration** wraps a **conversion**: suspension → apply converted resources → monitor
- **app-operator** translates an **App CR** into a **Chart CR** by resolving the **Catalog CR**; **chart-operator** reconciles the **Chart CR** into a Helm release
- `spec.catalog` on an **App CR** is the name of a **Catalog CR** on the MC; the **Catalog CR**'s `spec.repositories[].type` (`oci` or `helm`) determines which Flux source resource the **Conversion** produces
- A **Fetch** pulls an **App CR** and its **Catalog CR** from the MC and hands both dicts to a **Conversion**
- A **remote-cluster app** carries a kubeconfig Secret reference on both the **App CR** (`spec.kubeConfig.secret.name`) and the converted **HelmRelease** (`spec.kubeConfig.secretRef.name`)

## Example dialogue

> **Dev:** "Should we call it a migration or a conversion?"
> **Domain expert:** "Conversion is just the YAML translation. Migration is the full live operation — suspend, apply, monitor. Keep them separate."

> **Dev:** "Why does the valuesKey differ from what the App platform docs say?"
> **Domain expert:** "When you migrate, the ConfigMaps and Secrets get renamed following the GiantSwarm Flux convention. The new key names are `configmap-values.yaml` and `secret-values.yaml`, not `.data.values`."

## Scope decisions

- `spec.install`, `spec.upgrade`, `spec.rollback`, `spec.uninstall` on App CRs are always empty `{}` in observed real data. Decision: the converter omits them entirely; non-empty blocks are out of scope.
- `spec.config.configMap`, `spec.config.secret`, `spec.userConfig.configMap`, and `spec.userConfig.secret` sub-fields with an empty `name` are Go zero-value structs serialised to YAML — the App CR schema uses pointer-free structs so the field appears on the wire even when never populated. The converter skips these entries with a preflight warning, matching app-operator's behaviour. See ADR 0006.


## Flagged ambiguities

- "migration" was used loosely to mean both resource translation and the full live rollout — resolved: **conversion** = YAML translation only, **live migration** = full operational process.
