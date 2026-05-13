# appcr-to-helmrelease-converter

## What this is

A general-purpose Python CLI tool that converts a Giant Swarm **App CR** into Flux CD **HelmRelease + OCIRepository** resource pair. It is source-agnostic: you get the App CR from wherever (live cluster, file, collection) and pipe it in.

```bash
kubectl -n giantswarm get app my-app -o yaml | python main.py
cat my-app.yaml | python main.py
```

Output is two YAML documents separated by `---`.

## Current state

Single file (`main.py`), pyyaml dependency only. Reads one App CR from stdin, writes OCIRepository + HelmRelease to stdout.

## Intended evolution

### Near term: richer conversion CLI

- Multi-document stdin support (multiple App CRs in one pipe)
- CLI flags (e.g. OCI registry override, version pinning vs semver wildcard)
- Validation of the input App CR
- Tests

### Longer term: live migration wrapper

A separate layer (could be a subcommand or separate script) that wraps the converter and drives a full live migration:
1. Suspend the App CR and Chart CR on the MC (`app-operator.giantswarm.io/paused`, `chart-operator.giantswarm.io/paused` annotations)
2. Apply the converted OCIRepository + HelmRelease
3. Monitor the HelmRelease rollout
4. If issues arise: surface diagnostics (events, helm history, pod status)
5. Revert if needed (delete HR/OCIRepo, remove suspension annotations)

### Containerization

Package as a container image so the tool runs without a local Python environment.

## Conversion logic

**OCIRepository:**
- `spec.url`: `oci://gsoci.azurecr.io/charts/giantswarm/{app.spec.name}`
- `spec.ref.semver: x.x.x` — semver wildcard (version pinning via tag is commented out)
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

**Filtered out:**
- Annotations: `chart-operator.giantswarm.io/force-helm-upgrade`, `app-operator.giantswarm.io/paused`
- Labels: `app-operator.giantswarm.io/version`

## App CR configuration reference

Docs: https://docs.giantswarm.io/tutorials/fleet-management/app-platform/app-configuration/

Two intentional divergences from the docs worth knowing:

- **`valuesKey`**: The docs specify `.data.values` as the key inside ConfigMaps/Secrets on the App platform. The converter emits `configmap-values.yaml` / `secret-values.yaml` instead — this is the GiantSwarm Flux migration convention for the renamed data keys in the target ConfigMaps/Secrets.
- **`namespace`**: All App CR config references carry a `namespace` field. The converter drops it — Flux `valuesFrom` entries have no namespace field and expect the referenced objects to be co-located with the HelmRelease.

## Dev setup

```bash
virtualenv -p python3 virtualenv
source virtualenv/bin/activate
pip install -r requirements.txt
pip install -r requirements-test.txt
```

## Running tests

```bash
# Run tests
./virtualenv/bin/pytest

# Run tests with coverage report (enforces 100%)
./virtualenv/bin/pytest --cov --cov-report=term-missing
```

Coverage is configured in `pyproject.toml`: branch coverage enabled, 100% required, `if __name__ == '__main__':` excluded. Tests live in `tests/test_main.py`.
