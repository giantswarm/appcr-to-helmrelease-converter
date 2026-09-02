# appcr-to-helmrelease-converter

Converts App CRs into Flux CD resources (OCIRepository or HelmRepository + HelmRelease). Commands — `migrate` (phase one: convert, apply, monitor), `cleanup` (phase two: verify and delete the redundant App/Chart CRs), `suspend` (standalone: pause app-operator/chart-operator reconciliation only, no conversion), and `resume` (standalone: the inverse of `suspend`) — cluster access required. See `CONTEXT.md` for domain terminology, ADR 0015 for why offline conversion was removed, ADR 0027 for `cleanup`, ADR 0029 for `suspend`/`resume`, ADR 0030 for `--pull-secret`, and ADR 0031 for `--override-registry-url`.

```bash
python main.py migrate --name my-app --namespace giantswarm
python main.py migrate --name my-app --namespace giantswarm --context my-context
```

## Current state

`converter/` package (pure functions) + `fetcher/` package (I/O) + `migrator/` package (migration steps) + `preflight/` package + click CLI in `main.py`. Dependencies: pyyaml, click, kubernetes, rich.

## Intended evolution

### Longer term

- Diagnostics on migration failure: surface events, helm history, pod status

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
- A referenced ConfigMap/Secret with zero data keys is not an error: it's still added to `valuesFrom`, marked `optional: true`, no `valuesKey`, with a warning during Resolve. See ADR 0026.

**Filtered out:**
- Annotations: `chart-operator.giantswarm.io/force-helm-upgrade`, `app-operator.giantswarm.io/paused`
- Labels: `app-operator.giantswarm.io/version`, `policy.giantswarm.io/psp-status`

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

**9c. Suspend Chart CR** ✓ _Implemented: `SuspendChart` step in `migrator/suspend_chart.py`. Derives Chart CR name via `chart_cr_name()` (an alias of `converter.resources.release_name()` — strips `giantswarm.io/cluster` prefix/suffix only for remote apps; see item 22 / ADR 0028). GETs the Chart CR via the api to check current state (idempotent). Two-client problem solved in `main.py`: in-cluster apps reuse the MC `CustomObjectsApi`; remote-cluster apps use `load_wc_client()` which reads the kubeconfig Secret from MC via `CoreV1Api` and builds a separate WC `CustomObjectsApi`. `load_wc_client` errors handled cleanly (❌ + SystemExit)._

**9d. Apply** ✓ _Implemented: `ApplyFluxResources` step in `migrator/apply_flux_resources.py`. Server-side applies source resource (OCIRepository or HelmRepository) then HelmRelease to MC. On partial failure (source applied, HR fails), best-effort deletes source before re-raising so it doesn't leak. Revert suspends HR via merge-patch, deletes it, polls until finalizer clears (5s interval, 5min timeout), then deletes source only once HR is confirmed gone. Wired as the fourth step in the shared migration step loop in `main.py`._

**9e. Monitor** ✓ _Implemented (not live-tested): `MonitorHelmRelease` step in `migrator/monitor_helm_release.py`. Polls HelmRelease every 5s; returns on `Ready=True`; fails fast on `Stalled=True` (retries exhausted); retries up to 3× on transient API errors but raises immediately on 4xx; converts `KeyboardInterrupt` to `MigratorError` so LIFO revert fires. Rich `console.status()` spinner shows synthesized progress (attempt count, failure reason, retry indicator). Wired as 5th step in `main.py`. `_HR_GROUP/_HR_VERSION/_HR_PLURAL` consolidated into `migrator/__init__.py`._

**9f. Revert** ~~_(not started)_~~ _Obsolete: revert logic is now baked into each `MigrationStep.revert()` + `MigrationRunner.revert_all()` (LIFO). The skip-revert + print manual steps path belongs in the 9e Monitor step or `main.py` error handler. See ADR 0013._

**9g. Success cleanup** ✓ _Implemented: Clean-up section runs after `MonitorHelmRelease` succeeds. Flux-managed: prints explanation (Kustomization would recreate if deleted before gitops is updated) and kubectl commands to remove finalizers and delete Chart CR then App CR; Chart CR block notes "run against WORKLOAD cluster" with kubeconfig Secret location when remote. Non-Flux-managed: y/N prompt (default N); if confirmed, re-fetches CRs, re-applies paused annotations if missing, deletes Chart CR then App CR (remove finalizer → delete → poll until 404), reports partial failures. Plain function `delete_app_and_chart` + `flux_cleanup_message` in `migrator/cleanup.py`, outside MigrationStep/MigrationRunner. See ADR 0021._ `delete_app_and_chart` now tolerates a missing Chart CR: a 404 skips the chart block, still deletes the App CR, and adds an informational note; it returns `list[str]`. `migrate` inherits this fix as part of ADR 0027 (see item 21).

**10. Collapse to single `migrate` command** ✓ _Implemented: removed `convert`, `fetch`, `fetch-and-convert` commands, helpers (`_identify_docs`, `_dump`, `_check_and_emit`), and their tests. `migrate` Fetch section now displays the stripped App CR YAML. `fetcher/` package retained. See ADR 0015._

**11. Resolver layer with interactive valuesKey selection** ✓ _Implemented: `resolver/` package iterates all ConfigMap/Secret refs, looks each up via `CoreV1Api`, resolves `valuesKey` from actual data keys (single → auto; multiple → interactive prompt; `values.yaml` → `None` to omit). `Resolution` dataclass passed into `converter.convert()`. Cross-namespace valuesFrom preflight check. Wired between preflight and convert in `migrate`. See ADR 0016._

**12. Filter Flux labels and annotations by prefix** ✓ _Implemented: `fluxcd.io/` substring filter on both annotations and labels in `_build_helm_release_common()`. GS-specific blocklist extended with `app-operator.giantswarm.io/latest-configmap-version` and `app-operator.giantswarm.io/latest-secret-version`. See ADR 0017._

**13. Preflight warnings for OCI fallback and Flux-managed App CRs** ✓ _Implemented:_ Two new `PreflightWarning` checks in `preflight/__init__.py`: `check_oci_fallback` warns when catalog has no OCI type (converter will use HelmRepository path); `check_flux_managed` warns when App CR carries kustomize labels, instructing the operator to commit converted resources to gitops, remove the App CR from gitops, and manually strip finalizers `operatorkit.giantswarm.io/app-operator-app` (App CR) and `operatorkit.giantswarm.io/chart-operator-chart` (Chart CR). See ADR 0018.

**14. `--dry-run` and `--output FILENAME` flags for migrate** ✓ _Implemented: Two orthogonal flags on `migrate_cmd`. `--dry-run` (`is_flag=True`): prints `✅ Dry run complete — halting before live migration.` and exits 0 after displaying generated Flux resources, before the confirm prompt; all read-only steps (Fetch, Preflight, Resolve, Convert) still run. `--output FILENAME` (`click.Path(writable=True, dir_okay=False)`): serializes docs to plain multi-doc YAML once (reusing the cached `yaml_str`), writes to file, prints `✅ Conversion result saved to FILENAME!` with a leading blank line; runs before the dry-run exit so the combination writes the file and exits cleanly. See ADR 0019._

**15. `depends-on` annotation → `spec.dependsOn` on HelmRelease** ✓ _Implemented: `app-operator.giantswarm.io/depends-on` (comma-separated names) maps to `spec.dependsOn` entries on the generated HelmRelease; `dependsOn` appears before `install` in spec (alphabetical). Both `depends-on` and `depends-on-helmrelease` annotations stripped from HelmRelease metadata. Preflight verifies each dependency HelmRelease exists in the same namespace (existence only — readiness enforced by Flux at runtime); emits info note to operator. Annotation key shared as `_DEPENDS_ON_ANNOTATION` constant in both `fetcher/` and `converter/`; null-annotation guard (`(annotations or {}).get(...)`) prevents crash on explicit YAML `null`. `dependency_helm_releases: dict` added to `FetchResult`; pre-fetched in `fetcher.fetch()`, passed into `run_preflight` as new param. README updated. See ADR 0020._

**16. Revert note for pre-existing suspend state** ✓ _Implemented: see ADR 0022._ When a suspend step's revert is skipped because `_did_pause` / `_did_disable_reconcile` is `False` and the state was already present before this run, emit a note with a `kubectl` command to undo manually. `MigrationStep` gains `revert_note: str | None`; `MigrationRunner.revert_all()` collects notes into `self.revert_notes`; `main.py` prints them to stderr after the revert block.

**17. Drop legacy `psp-removal-patch*` extraConfigs entries** ✓ _Implemented: see ADR 0024._ `calculate_values_from()` unconditionally excludes any `spec.extraConfigs[]` entry with `kind: ConfigMap` (case-insensitive), `name` starting with `psp-removal-patch` (covers both the default name and app-admission-controller's per-app suffixed names, e.g. `psp-removal-patch-datadog`), and `namespace` equal to the App CR's own namespace (defaulting to it when absent) — a pre-Kubernetes-1.25 PodSecurityPolicy patch with no equivalent once PSPs were removed. Scoped to `extraConfigs` only — `spec.config`/`spec.userConfig` are not checked. `check_psp_removal_patch` preflight warning added to `_CHECKS`; fetch/resolve are untouched, filtering happens only when building `valuesFrom`. Shared `is_psp_removal_patch()` predicate in `converter/values_from.py`, imported by `preflight/__init__.py`.

**18. Drop legacy `policy.giantswarm.io/psp-status` label** ✓ _Implemented: see ADR 0017 (amended)._ `policy.giantswarm.io/psp-status` added to `_LABEL_BLOCKLIST` in `converter/resources.py` — a companion to item 17's `psp-removal-patch` cleanup, records the outcome of that same legacy patch and is meaningless once PSPs are gone. Dropped silently, same as every other blocklist entry; no preflight warning (unlike the extraConfigs removal, this is inert metadata with no functional effect).

**19. Extend filtered label/annotation propagation to OCIRepository and HelmRepository** ✓ _Implemented: see ADR 0025._ Extracted the ADR 0017 filter (`_ANNOTATION_BLOCKLIST`/`_LABEL_BLOCKLIST` + `fluxcd.io/` substring drop) into a shared `_filtered_metadata(app)` helper in `converter/resources.py`, now used by `build_oci_repository`, `_build_helm_repository`, and `_build_helm_release_common` — previously the source resources only ever got bare `name`/`namespace` metadata. `app-operator.giantswarm.io/depends-on` consumption into `spec.dependsOn` remains HelmRelease-only (ADR 0020); the annotation is still dropped from all three via the shared blocklist. `TestBuildOciRepository` and `TestBuildHelmReleaseAndHelmRepo` gained label/annotation test cases mirroring `TestBuildHelmRelease`.

**20. Drop legacy `app-operator.giantswarm.io/trigger-reconciliation` annotation** ✓ _Implemented: see ADR 0017 (amended)._ Added to `_ANNOTATION_BLOCKLIST` in `converter/resources.py` — a fire-and-forget instruction to app-operator with no Flux equivalent, dropped silently like every other blocklist entry (no preflight warning).

**21. Standalone `cleanup` command** ✓ _Implemented: see ADR 0027._ `python main.py cleanup --name X --namespace Y [--context ctx] [--dry-run] [--assume-yes/-y]` — phase two of the migration, run once gitops has adopted the converted resources. Builds one MC client and GETs the App CR directly instead of going through `fetcher.fetch()`, which would hard-error on a Catalog CR deleted after migration and fetch config sources that cleanup doesn't need. `verify_migration(api, app) -> list[str]` in `migrator/cleanup.py` returns every failing check in one call (empty = pass) and makes exactly one API call — the HelmRelease GET; everything else reads the already-fetched App CR. Checks always run: HelmRelease exists at the App CR's own name/namespace, is `Ready`, is not suspended, and `status.observedGeneration == metadata.generation`. Two more run only when the App CR is Flux-managed: the HelmRelease carries `kustomize.toolkit.fluxcd.io/name`/`/namespace` (proof gitops adopted it — ADR 0017/0025 strip every `fluxcd.io/` label and annotation from generated resources, so those labels can only have been stamped on by Flux applying the HelmRelease out of the gitops repository) and the App CR carries `kustomize.toolkit.fluxcd.io/reconcile: disabled` (proof Flux won't re-apply it after deletion). `--dry-run` runs every check and stops; `--assume-yes` skips only the confirmation prompt, never a check. `is_flux_managed()` moved into `migrator/__init__.py`, replacing three duplicated copies (`main.py`'s clean-up branch, `DisableFluxReconcileApp`, and this command's gitops-adoption check).

**22. Fix cluster-prefix stripping predicate for releaseName and Chart CR name** ✓ _Implemented: see ADR 0028 and [issue #12](https://github.com/giantswarm/appcr-to-helmrelease-converter/issues/12)._ `release_name(app)` (`converter/resources.py`, formerly the private `_release_name`) now strips the `giantswarm.io/cluster` prefix/suffix only when the app is remote (`_is_remote(app)`, gated on `spec.kubeConfig.inCluster`) — not merely when the label is present. In-cluster, per-workload-cluster component apps sharing an MC namespace (e.g. `operations-auth-bundle`) keep their unstripped name as both `releaseName` and the Chart CR name. `migrator.chart_cr_name` is now a plain alias (`from converter.resources import release_name as chart_cr_name`) instead of a second copy of the same logic — `migrator` already depends on `converter` being dependency-free, so the direction is safe and doesn't pull `kubernetes` into the converter package. Not added: a `--release-name` CLI override flag — the corrected heuristic covers every known pattern; revisit only if a new namespace pattern breaks it again.

**23. Standalone `suspend` command** ✓ _Implemented: see ADR 0029._ `python main.py suspend --name X --namespace Y [--context ctx]` — runs only `SuspendApp` + `SuspendChart` (CONTEXT.md's "Suspension" term exactly: the two paused-annotation steps), skipping `DisableFluxReconcileApp`, which is a separate Flux-specific concern. No `--dry-run`/`--assume-yes`/confirmation prompt and no revert-on-failure: both steps are idempotent, so a failed run is reported (❌ + exit 1) and a re-run after fixing the issue picks up cleanly. Reuses `_get_app_or_exit()` in `main.py` (extracted from `cleanup`'s inline fetch block, now shared by both) for the direct App-CR GET — a 404 there is an info line + exit 0. `SuspendChart` gained a `tolerate_missing: bool = False` param for 404-tolerance on the Chart CR GET (`skipped`/`skip_message`, same pattern as `DisableFluxReconcileApp`); only `suspend_cmd` passes `tolerate_missing=True` — `migrate` does not opt in, so it still hard-fails (with its usual revert prompt) when the Chart CR doesn't exist, same as before this command existed. Already-paused steps print "already set, nothing to do" rather than reusing the steps' `revert_note` text, which is worded for `migrate`'s revert path and doesn't fit a command that never reverts; this reporting (`_report_step_result`) and the `load_client`/`_resolve_chart_api` error-exit wrapping (`_load_client_or_exit`, `_resolve_chart_api_or_exit`) are shared helpers used by all three commands, not copy-pasted per command. `_delete_and_report` prints "✅ App CR deleted." instead of the blanket "and Chart CR deleted." whenever `delete_app_and_chart` returned a skip note. Two knock-on effects on `migrate`/`cleanup`'s own pre-existing output, both intentional bug fixes made while deduplicating: `migrate`'s step loop now also prints the "already set" line for an idempotent suspend step instead of a misleading `✅`, and `migrate`'s non-Flux delete path picks up the same "App CR deleted." wording fix as `cleanup` since both call `_delete_and_report`. See ADR 0029.

**24. Standalone `resume` command** ✓ _Implemented: see ADR 0029._ `python main.py resume --name X --namespace Y [--context ctx]` — the inverse of `suspend`: clears `app-operator.giantswarm.io/paused` and `chart-operator.giantswarm.io/paused` instead of setting them. Same flags/no-prompt/no-`--dry-run` reasoning as `suspend`, same scope exclusion of `DisableFluxReconcileApp`. Deliberately *not* built on `MigrationStep.revert()` — `revert()`'s `_did_pause` guard is only set by a prior `apply()` in the same instance, so a freshly-built step calling `.revert()` would always no-op; a `force` param on `revert()` was considered and rejected too, since bypassing that guard would still need its own live-state re-check, just grafted onto a method whose contract doesn't otherwise fit. Implemented instead as a standalone `resume_app_and_chart(api, chart_api, app) -> list[str]` in a new `migrator/resume.py`, mirroring `migrator/cleanup.py`'s `delete_app_and_chart` shape. Unconditionally patches each annotation to clear it (no GET-first precheck — clearing an absent annotation is a harmless no-op), so unlike `suspend` it can't distinguish "just resumed" from "was already resumed" and reports one message either way. The Chart CR PATCH itself can still 404 (you can't patch a resource that doesn't exist even though clearing an absent annotation on an existing one is a no-op) — caught and added as a skip note, same pattern as `delete_app_and_chart`'s Chart-CR-not-found note. CONTEXT.md gained a **Resume** term as the inverse of **Suspension**.

**25. `--pull-secret` for authenticated catalogs** ✓ _Implemented: see ADR 0030._ `migrate --pull-secret NAME` emits `spec.secretRef.name` on the generated OCIRepository (Path A) or HelmRepository (Path B) — the HelmRelease gets nothing, since it pulls the chart through the source. Name only, never `namespace/name`: Flux resolves `secretRef` only in the source resource's own namespace, which `_filtered_metadata` sets to the App CR's namespace. `fetcher.fetch()` gained a `pull_secret` param and GETs the Secret through the existing `_fetch_one` helper into `FetchResult.pull_secret`. `preflight.check_pull_secret` reports a `PreflightError` when the Secret is absent, and a `PreflightWarning` when its shape does not match the path (OCI wants `type: kubernetes.io/dockerconfigjson` or `/dockercfg`; HelmRepository wants `username` + `password` data keys) — shape is a warning because those rules are a reading of the Flux docs, not a guarantee, and differ again for a `type: oci` HelmRepository. `converter._catalog_has_oci` became public `catalog_has_oci`, replacing the duplicated predicate inside `check_oci_fallback`. Not in scope: `certSecretRef`/TLS, `spec.provider` for cloud registries, `serviceAccountName` on the source, creating or copying the Secret, and `cleanup` (it converts nothing).

**26. `--override-registry-url` for mirrored registries** ✓ _Implemented: see ADR 0031._ `migrate --override-registry-url HOST` replaces the **host** of the one registry URL the converter reads — OCI first, else Helm, with the `spec.storage` fallback — leaving the path untouched: only the host/port changes, on that single `spec.repositories[]` entry (or `spec.storage`); sibling entries are left alone. `migrate`-only — `cleanup`, `suspend`, `resume` convert nothing, so they don't get it. The value accepts a bare host, a host:port, or either with a scheme; a click option callback strips one trailing slash then rejects a value carrying a path, or an empty value, with `BadParameter` at parse time. The emitted scheme is never taken from the option value: an OCIRepository is always `oci://` regardless of the catalog's own scheme, and a HelmRepository keeps the catalog's scheme; a scheme in the value that contradicts the conversion path — `oci://` against a Helm-only catalog, or an HTTP scheme against an OCI catalog — is a `PreflightError` from the new `check_registry_override` (scheme comparison is case-insensitive; it reads the catalog's Helm scheme through `converter.resources.helm_url_from_catalog`, renamed from `_helm_url_from_catalog` so no package imports a private name across a boundary). The rewrite itself is a pure `converter.override_catalog_registry(catalog, host) -> dict` returning a copy of the Catalog CR with the host replaced where it was read from; `main.py` calls it between Resolve and `convert()` on `result.catalog`, so `convert()` and the five `build_*` signatures are unchanged. `catalog_has_oci` moved from `converter/__init__.py` to `converter/resources.py` (re-exported; `preflight`'s import is unchanged). `main.py` prints one info line `ℹ️  Registry host overridden to <host>.` at the top of the "Generated Flux resources" section every time the flag is given, even when the host matches what the catalog already had; no warning fires when the flag is used without `--pull-secret`. Not in scope: rewriting the path, rewriting the chart name, a scheme override, `spec.type: oci` on a HelmRepository, checking the mirror is reachable or the chart is in it, and a per-catalog/config-file mapping of registries.

## Dev setup

```bash
uv sync
```

## Running tests

```bash
uv run pytest
uv run pytest --cov --cov-report=term-missing
```

Coverage: branch coverage, 100% required, `if __name__ == '__main__':` excluded. Tests split across `tests/test_apply_flux_resources.py`, `tests/test_cleanup.py`, `tests/test_converter.py`, `tests/test_fetcher.py`, `tests/test_main.py`, `tests/test_migrator.py`, `tests/test_monitor_helm_release.py`, `tests/test_preflight.py`, `tests/test_resolver.py`, `tests/test_resources.py`, `tests/test_values_from.py`.

**Never run tests against a real Kubernetes cluster.** All code touching the `kubernetes` client must be testable via mocks only. Always patch `kubernetes.config.load_kube_config` and `kubernetes.client.*` in tests. No real kubeconfig loading, no real network calls, no real cluster context.
