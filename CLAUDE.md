# appcr-to-helmrelease-converter

Reads one App CR from stdin, writes OCIRepository + HelmRelease to stdout (two YAML documents separated by `---`). See `CONTEXT.md` for domain terminology.

```bash
kubectl -n giantswarm get app my-app -o yaml | python main.py
cat my-app.yaml | python main.py
```

## Current state

Single file (`main.py`), pyyaml dependency only.

## Intended evolution

### Near term

- Multi-document stdin support (multiple App CRs in one pipe)
- CLI flags (e.g. OCI registry override, version pinning vs semver wildcard)
- Input validation
- Tests

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

**OCIRepository:**
- `spec.url`: `oci://gsoci.azurecr.io/charts/giantswarm/{app.spec.name}`
- `spec.ref.semver`: semver wildcard (version pinning via tag is commented out)
- interval: 10m, provider: generic

**HelmRelease:**
- `spec.storageNamespace` + `spec.targetNamespace` ← `app.spec.namespace`
- `spec.releaseName` ← `app.metadata.name`
- `spec.chartRef` points to the OCIRepository by name + namespace
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

Coverage: branch coverage, 100% required, `if __name__ == '__main__':` excluded. Tests in `tests/test_main.py`.
