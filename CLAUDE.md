# appcr-to-helmrelease-converter

Converts App CRs into Flux CD resources (OCIRepository or HelmRepository + HelmRelease). Single `migrate` command — cluster access required. See `CONTEXT.md` for domain terminology and ADR 0015 for why offline conversion was removed.

```bash
python main.py migrate --name my-app --namespace giantswarm
python main.py migrate --name my-app --namespace giantswarm --context my-context
```

## Current state

`converter/` package (pure functions) + `fetcher/` package (I/O) + `migrator/` package (migration steps) + `preflight/` package + click CLI in `main.py`. Dependencies: pyyaml, click, kubernetes, rich.

## Intended evolution

### Near term

- Resolver layer: `resolver/` package resolves `valuesKey` from live cluster data; interactive prompt when multiple keys; `Resolution` passed into converter (ADR 0016)
- Success cleanup (9g): Flux-managed apps print manual deletion instructions; non-Flux-managed apps prompt and delete

### Longer term

- Diagnostics on migration failure: surface events, helm history, pod status
- Success cleanup (9g)

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

**1b. `convert` command — multi-doc YAML input** ~~✓ Implemented~~ _Superseded by ADR 0015: command removed. See ADR 0011._

**1c. `fetch` command** ~~✓ Implemented~~ _Superseded by ADR 0015: command removed. See ADR 0008, ADR 0010._

**1d. `fetch-and-convert` command** ~~✓ Implemented~~ _Superseded by ADR 0015: command removed. See ADR 0008._

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

**9e. Monitor** ✓ _Implemented (not live-tested): `MonitorHelmRelease` step in `migrator/monitor_helm_release.py`. Polls HelmRelease every 5s; returns on `Ready=True`; fails fast on `Stalled=True` (retries exhausted); retries up to 3× on transient API errors but raises immediately on 4xx; converts `KeyboardInterrupt` to `MigratorError` so LIFO revert fires. Rich `console.status()` spinner shows synthesized progress (attempt count, failure reason, retry indicator). Wired as 5th step in `main.py`. `_HR_GROUP/_HR_VERSION/_HR_PLURAL` consolidated into `migrator/__init__.py`._

**9f. Revert** ~~_(not started)_~~ _Obsolete: revert logic is now baked into each `MigrationStep.revert()` + `MigrationRunner.revert_all()` (LIFO). The skip-revert + print manual steps path belongs in the 9e Monitor step or `main.py` error handler. See ADR 0013._

**9g. Success cleanup** _(not started)_ Flux-managed: print instructions to manually delete App CR + Chart CR. Non-Flux-managed: prompt and delete both.

**10. Collapse to single `migrate` command** ✓ _Implemented: removed `convert`, `fetch`, `fetch-and-convert` commands, helpers (`_identify_docs`, `_dump`, `_check_and_emit`), and their tests. `migrate` Fetch section now displays the stripped App CR YAML. `fetcher/` package retained. See ADR 0015._

**11. Resolver layer with interactive valuesKey selection** ✓ _Implemented: `resolver/` package iterates all ConfigMap/Secret refs, looks each up via `CoreV1Api`, resolves `valuesKey` from actual data keys (single → auto; multiple → interactive prompt; `values.yaml` → `None` to omit). `Resolution` dataclass passed into `converter.convert()`. Cross-namespace valuesFrom preflight check. Wired between preflight and convert in `migrate`. See ADR 0016._

**12. Filter Flux labels and annotations by prefix** ✓ _Implemented: `fluxcd.io/` substring filter on both annotations and labels in `_build_helm_release_common()`. GS-specific blocklist extended with `app-operator.giantswarm.io/latest-configmap-version` and `app-operator.giantswarm.io/latest-secret-version`. See ADR 0017._

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
