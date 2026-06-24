# appcr-to-helmrelease-converter

A CLI tool that migrates Giant Swarm **App CRs** to **Flux CD resources** on a live cluster.

It fetches the App CR and its Catalog CR from the cluster, converts them into the equivalent Flux resources (an `OCIRepository` or `HelmRepository` plus a `HelmRelease`), applies them, and monitors the rollout — suspending the old App CR along the way so chart-operator stops managing it.

## Prerequisites

- Python 3.12+
- `kubectl` access to the Management Cluster (or a valid `--context`)
- The target app must already be running (the tool reads live cluster state)

## Setup

```bash
virtualenv -p python3 virtualenv
source virtualenv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py migrate --name <app-name> --namespace <namespace>
python main.py migrate --name <app-name> --namespace <namespace> --context <kubeconfig-context>
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--name` | Name of the App CR (required) |
| `--namespace` | Namespace of the App CR (required) |
| `--context` | kubeconfig context to use (default: current context) |
| `--dry-run` | Stop after showing the generated Flux YAML — no cluster mutations |
| `--output FILE` | Write the generated Flux YAML to a file (independent of `--dry-run`) |

**Example — preview before committing:**

```bash
python main.py migrate --name loki --namespace monitoring --dry-run --output loki-flux.yaml
```

**Example — live migration:**

```bash
python main.py migrate --name loki --namespace monitoring
```

The tool will:

1. Fetch the App CR and its Catalog CR from the cluster
2. Run preflight checks — including verifying that any `depends-on` dependencies exist as HelmReleases — and print any warnings
3. Resolve `valuesKey` for each ConfigMap/Secret referenced by the app — prompts you to choose when a resource has multiple data keys
4. Show you the generated Flux YAML (and save it if `--output` is set), then ask for confirmation
5. Suspend the App CR (and its Chart CR on the workload cluster if applicable)
6. Apply the new Flux resources to the cluster
7. Watch the HelmRelease until it becomes ready — on failure, asks whether to roll back

## What gets generated

Depending on the Catalog type:

- **OCI catalog** → `OCIRepository` + `HelmRelease`
- **Helm catalog** → `HelmRepository` + `HelmRelease`

Config sources (`spec.config`, `spec.userConfig`, `spec.extraConfigs`) are carried over as `valuesFrom` entries on the HelmRelease, with priorities preserved. Flux- and GS-specific labels and annotations are filtered out of the output.

If the App CR carries `app-operator.giantswarm.io/depends-on`, the converted HelmRelease will have a `spec.dependsOn` list. The preflight check verifies that each referenced dependency HelmRelease exists — existence only, not readiness. Flux enforces ordering and waits for dependencies to become Ready at runtime.

## Running tests

```bash
./virtualenv/bin/pytest
./virtualenv/bin/pytest --cov --cov-report=term-missing
```
