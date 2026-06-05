# appcr-to-helmrelease-converter

Converts App CRs into Flux CD resources (OCIRepository or HelmRepository + HelmRelease). Three commands at different capability levels — see `CONTEXT.md` for domain terminology.

```bash
# Offline: multi-doc YAML (App CR + Catalog CR separated by ---) from stdin or file
cat my-app-and-catalog.yaml | python main.py convert
python main.py convert my-app-and-catalog.yaml

# Fetch CRs from MC as multi-doc YAML (pipeable into convert)
python main.py fetch --name my-app --namespace giantswarm
python main.py fetch --name my-app --namespace giantswarm --context my-context

# Fetch and convert in one step
python main.py fetch-and-convert --name my-app --namespace giantswarm
python main.py fetch-and-convert --name my-app --namespace giantswarm --context my-context

# Full live migration (planned)
python main.py migrate --name my-app --namespace giantswarm
```

## Current state

`converter/` package (pure functions) + `fetcher/` package (I/O) + click CLI in `main.py`. Dependencies: pyyaml, click, kubernetes.

## Intended evolution

### Near term

- Catalog CR as required second input; conversion path from `spec.repositories[].type` (see ADR 0007)
- `fetch-and-convert` command: `--name`, `--namespace`, optional `--context` (see ADR 0008, ADR 0009)
- Input validation

### Longer term: live migration wrapper

A subcommand or separate script that drives a full live migration:
1. Suspend the App CR and Chart CR on the MC (`app-operator.giantswarm.io/paused`, `chart-operator.giantswarm.io/paused` annotations)
2. Apply the converted OCIRepository + HelmRelease
3. Monitor the HelmRelease rollout
4. Surface diagnostics on failure (events, helm history, pod status)
5. Revert if needed (delete HR/OCIRepo, remove suspension annotations)

### Containerization

Package as a container image so the tool runs without a local Python environment.

## Conversion rules

Two conversion paths depending on `catalog_dict["spec"]["repositories"][].type`. The Catalog CR is always required as a second input alongside the App CR. GS vs non-GS catalog distinction is human context only — the converter reads the type directly from the Catalog CR.

**Path A — `type: oci` → OCIRepository + HelmRelease**

OCIRepository:
- `spec.url`: `{catalog_dict["spec"]["repositories"][type=oci].URL.rstrip("/")}/{app.spec.name}`
- `spec.ref.tag`: `app.spec.version` (exact pin; empty or missing version is a hard error)
- interval: 10m, provider: generic

**Path B — `type: helm` → HelmRepository + HelmRelease** _(planned — see ADR 0007)_

HelmRepository:
- `spec.url`: first `catalog_dict["spec"]["repositories"][type=helm].URL`; preflight warning if multiple `helm` entries
- `metadata.name` + `metadata.namespace`: same as the HelmRelease

HelmRelease (Path B):
- `spec.chart.spec.chart` ← `app.spec.name`
- `spec.chart.spec.version` ← `app.spec.version`
- `spec.chart.spec.sourceRef`: points to the HelmRepository by name + namespace

**HelmRelease (both paths):**
- `spec.storageNamespace` + `spec.targetNamespace` ← `app.spec.namespace`
- `spec.releaseName` ← `app.metadata.name`
- `spec.chartRef` points to the OCIRepository by name + namespace (Path A)
- `spec.chart.spec.sourceRef` points to the HelmRepository (Path B — see ADR 0007)
- Upgrade remediation: `remediateLastFailure: true`, strategy: rollback
- Install remediation: `remediateLastFailure: false`, retries: 10

**valuesFrom** (sorted ascending by priority, then by kind):

| Source | Kind | Priority |
|---|---|---|
| `spec.extraConfigs[]` | as specified | 25 (default) |
| `spec.config.configMap` | ConfigMap | 50 |
| `spec.config.secret` | Secret | 50 |
| `spec.userConfig.configMap` | ConfigMap | 100 |
| `spec.userConfig.secret` | Secret | 100 |

- `valuesKey`: `configmap-values.yaml` for ConfigMaps, `secret-values.yaml` for Secrets
- `namespace` is dropped from all valuesFrom entries (Flux expects co-located objects)
- References with an empty or absent `name` under `spec.config` or `spec.userConfig` are skipped with a preflight warning — they are Go zero-value structs that app-operator also ignores

**Filtered out:**
- Annotations: `chart-operator.giantswarm.io/force-helm-upgrade`, `app-operator.giantswarm.io/paused`
- Labels: `app-operator.giantswarm.io/version`

## Feature parity backlog

Fields present in real App CRs that are not yet handled. Each item is a separate session scope.

**1a. converter — Catalog CR integration (Path A + Path B)** ✓ _Implemented. `convert()` branches on catalog type. OCI present → Path A: `build_helm_release_and_oci_repo()` → [OCIRepository, HelmRelease with `spec.chartRef`]. Helm only → Path B: `build_helm_release_and_helm_repo()` → [HelmRepository, HelmRelease with `spec.chart.spec`]. Both paths share boilerplate via `_build_helm_release_common()`. Preflight warning in `main.py` when catalog has multiple `helm` entries (uses first). `spec.storage` fallback supported for both paths. See ADR 0007._

**1b. `convert` command — multi-doc YAML input** ✓ _Implemented: `_identify_docs()` with `yaml.safe_load_all()`, identifies by `kind` + `apiVersion: application.giantswarm.io/v1alpha1`, hard-errors on missing or duplicate docs, order-independent. See ADR 0007, ADR 0011._

**1c. `fetch` command** ✓ _Implemented: `--name`, `--namespace`, optional `--context`; emits Catalog CR first, App CR second; strips server-side metadata fields and `kubectl.kubernetes.io/last-applied-configuration` annotation in `main.py`. See ADR 0008, ADR 0010._

**1d. `fetch-and-convert` command** ✓ _Implemented: `--name`, `--namespace`, optional `--context`; calls `fetcher.fetch()`, runs multiple-helm-repo and preflight checks, then converts and dumps Flux YAML. Preflight errors exit nonzero; warnings print and continue. No server-field stripping — raw CRs go straight to converter. See ADR 0008._

**3. kubeConfig — remote cluster targeting** ✓ _Implemented: emit `spec.kubeConfig.secretRef.name` on HelmRelease when `inCluster: false`. See commit 199bf5b._

**4. version — pin vs semver wildcard** ✓ _Implemented: always emit `ref.tag: spec.version`; empty/missing version is a hard error. See ADR 0002._

**5. namespaceConfig** ✓ _Decided: drop with a stderr warning. Target namespace already exists from app-operator; HelmRelease has no equivalent. Warning emitted from `main.py` (I/O layer). See ADR 0004._

**6. install/upgrade/rollback/uninstall blocks** ✓ _Decided: omit. All 51 occurrences in real data are empty `{}`. The converter emits nothing for these fields; non-empty blocks are not a supported input._

**7. Pre-flight checks / structured logging** _(deferred — not a priority)_
Ad-hoc `click.echo(..., err=True)` warnings (e.g. for `namespaceConfig`) should be replaced with a proper diagnostic layer: structured warnings, a `--strict` flag that turns warnings into errors, and/or a pre-flight validation pass that reports all issues before conversion begins.

**8. fetcher/ package — cluster fetch for fetch-and-convert** ✓ _Implemented: `fetcher/` I/O package with `fetch(name, namespace, context=None) -> (app_dict, catalog_dict)`. Wraps ApiException in FetchError. See ADR 0009._

**9. `migrate` command** _(high-level design done — see ADR 0012. Implementation split into sub-tasks below.)_

**9a. `migrate` command skeleton** ✓ _Implemented: click command with `--name`, `--namespace`, `--context`; reuses `fetcher.fetch()`, `run_preflight()`, `converter.convert()`; section headers via Rich (`▌ Title ───`), syntax-highlighted YAML (monokai), confirmation prompt. ConfigException from invalid context caught and surfaced cleanly. Read-only — no live mutations._

**9b. Suspend App CR** ✓ _Implemented: `DisableFluxReconcileApp` + `SuspendApp` steps in `migrator/` package. Detects Flux-managed App CR (`kustomize.toolkit.fluxcd.io/name` + `namespace` labels); adds `kustomize.toolkit.fluxcd.io/reconcile: disabled` if needed; adds `app-operator.giantswarm.io/paused` annotation. Idempotent. See commit 2bb096d._

**9c. Suspend Chart CR** ✓ _Implemented: `SuspendChart` step in `migrator/suspend_chart.py`. Derives Chart CR name via `chart_cr_name()` (strips `giantswarm.io/cluster` prefix/suffix). GETs the Chart CR via the api to check current state (idempotent). Two-client problem solved in `main.py`: in-cluster apps reuse the MC `CustomObjectsApi`; remote-cluster apps use `load_wc_client()` which reads the kubeconfig Secret from MC via `CoreV1Api` and builds a separate WC `CustomObjectsApi`. `load_wc_client` errors handled cleanly (❌ + SystemExit)._

**9d. Apply** ✓ _Implemented: `ApplyFluxResources` step in `migrator/apply_flux_resources.py`. Server-side applies source resource (OCIRepository or HelmRepository) then HelmRelease to MC. On partial failure (source applied, HR fails), best-effort deletes source before re-raising so it doesn't leak. Revert suspends HR via merge-patch, deletes it, polls until finalizer clears (5s interval, 5min timeout), then deletes source only once HR is confirmed gone. Wired as the fourth step in the shared migration step loop in `main.py`._

**9e. Monitor** _(not started)_ Watch HelmRelease status until ready or user aborts.

**9f. Revert** ~~_(not started)_~~ _Obsolete: revert logic is now baked into each `MigrationStep.revert()` + `MigrationRunner.revert_all()` (LIFO). The skip-revert + print manual steps path belongs in the 9e Monitor step or `main.py` error handler. See ADR 0013._

**9g. Success cleanup** _(not started)_ Flux-managed: print instructions to manually delete App CR + Chart CR. Non-Flux-managed: prompt and delete both.

## Dev setup

```bash
virtualenv -p python3 virtualenv
source virtualenv/bin/activate
pip install -r requirements.txt
pip install -r requirements-test.txt
```

## Running tests

```bash
./virtualenv/bin/pytest
./virtualenv/bin/pytest --cov --cov-report=term-missing
```

Coverage: branch coverage, 100% required, `if __name__ == '__main__':` excluded. Tests split across `tests/test_values_from.py`, `tests/test_resources.py`, `tests/test_converter.py`, `tests/test_main.py`.

**Never run tests against a real Kubernetes cluster.** All code touching the `kubernetes` client must be testable via mocks only. Always patch `kubernetes.config.load_kube_config` and `kubernetes.client.*` in tests. No real kubeconfig loading, no real network calls, no real cluster context.
