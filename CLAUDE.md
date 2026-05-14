# appcr-to-helmrelease-converter

Reads one App CR from stdin (or a file), writes OCIRepository + HelmRelease to stdout (two YAML documents separated by `---`). See `CONTEXT.md` for domain terminology.

```bash
kubectl -n giantswarm get app my-app -o yaml | python main.py convert
cat my-app.yaml | python main.py convert
python main.py convert my-app.yaml
```

## Current state

`converter/` package (pure functions) + click CLI in `main.py`. Dependencies: pyyaml, click.

## Intended evolution

### Near term

- Multi-document stdin support (multiple App CRs in one pipe)
- CLI flags (e.g. OCI registry override)
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

Two conversion paths depending on `app.spec.catalog`. See `CONTEXT.md` for GS catalog vs non-GS catalog definitions.

**Path A — GS catalog → OCIRepository + HelmRelease**

OCIRepository:
- `spec.url`: `oci://gsoci.azurecr.io/charts/{catalog}/{app.spec.name}`
- `spec.ref.tag`: `app.spec.version` (exact pin; empty or missing version is a hard error)
- interval: 10m, provider: generic

**Path B — non-GS catalog → HelmRepository + HelmRelease** _(not yet implemented)_

HelmRepository:
- `spec.url`: looked up from the Catalog CR on the MC (`spec.repositories[type=helm].URL`)
- The Catalog CR name equals `app.spec.catalog`

**HelmRelease (both paths):**
- `spec.storageNamespace` + `spec.targetNamespace` ← `app.spec.namespace`
- `spec.releaseName` ← `app.metadata.name`
- `spec.chartRef` points to the OCIRepository by name + namespace (Path A)
- `spec.chart.spec.sourceRef` points to the HelmRepository (Path B, not yet implemented)
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

**Filtered out:**
- Annotations: `chart-operator.giantswarm.io/force-helm-upgrade`, `app-operator.giantswarm.io/paused`
- Labels: `app-operator.giantswarm.io/version`

## Feature parity backlog

Fields present in real App CRs that are not yet handled. Each item is a separate session scope.

**1. catalog → OCI URL (GS catalogs)**
Currently hardcoded to `giantswarm` catalog. Needs to derive the OCI URL from `app.spec.catalog` for all known GS catalogs. Pattern: `oci://gsoci.azurecr.io/charts/{catalog}/{chart}`.

**2. catalog → HelmRepository (non-GS catalogs)**
Non-GS catalogs require a HelmRepository source instead of OCIRepository. The HTTP URL must be looked up from the Catalog CR on the MC. Requires the converter to accept Catalog CR input or a URL flag.

**3. kubeConfig — remote cluster targeting**
`spec.kubeConfig.inCluster: false` → emit `spec.kubeConfig.secretRef.name` on the HelmRelease pointing to the same kubeconfig Secret. `inCluster: true` → no change needed.
_Open question: is the kubeconfig Secret format compatible between app-operator and Flux?_

**4. version — pin vs semver wildcard** ✓ _Implemented: always emit `ref.tag: spec.version`; empty/missing version is a hard error. See ADR 0002._

**5. namespaceConfig**
`spec.namespaceConfig` (annotations + labels on the target namespace) has no HelmRelease equivalent. Decide: emit a separate `Namespace` resource, warn and skip, or error.

**6. install/upgrade/rollback/uninstall blocks**
All 51 occurrences in the real data are empty `{}`. Safe to ignore — emit nothing.

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
