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

**Management Cluster (MC)**:
The Kubernetes cluster that runs app-operator and chart-operator, where App CRs live.
_Avoid_: management plane, control cluster

**Live migration**:
The broader operational process of suspending an App CR, applying the converted Flux resources, and monitoring the rollout. Distinct from a conversion, which is only the resource translation step.
_Avoid_: migration (ambiguous — always qualify as "live migration")

**Suspension**:
Pausing app-operator and chart-operator reconciliation on an App CR / Chart CR by setting the respective `paused` annotations, so Flux can take ownership without conflict.

## Relationships

- One **App CR** produces exactly one **OCIRepository** + one **HelmRelease**
- A **HelmRelease** references its **OCIRepository** via `spec.chartRef`
- **valuesFrom** entries are derived from `spec.extraConfigs`, `spec.config`, and `spec.userConfig` on the **App CR**, merged in ascending **priority** order
- A **live migration** wraps a **conversion**: suspension → apply converted resources → monitor

## Example dialogue

> **Dev:** "Should we call it a migration or a conversion?"
> **Domain expert:** "Conversion is just the YAML translation. Migration is the full live operation — suspend, apply, monitor. Keep them separate."

> **Dev:** "Why does the valuesKey differ from what the App platform docs say?"
> **Domain expert:** "When you migrate, the ConfigMaps and Secrets get renamed following the GiantSwarm Flux convention. The new key names are `configmap-values.yaml` and `secret-values.yaml`, not `.data.values`."

## Flagged ambiguities

- "migration" was used loosely to mean both resource translation and the full live rollout — resolved: **conversion** = YAML translation only, **live migration** = full operational process.
