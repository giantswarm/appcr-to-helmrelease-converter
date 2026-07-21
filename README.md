# appcr-to-helmrelease-converter

A CLI tool that migrates Giant Swarm **App CRs** to **Flux CD resources** on a live cluster.

It fetches the App CR and its Catalog CR from the cluster, converts them into the equivalent Flux resources (an `OCIRepository` or `HelmRepository` plus a `HelmRelease`), applies them, and monitors the rollout — suspending the old App CR along the way so chart-operator stops managing it.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `kubectl` access to the Management Cluster (or a valid `--context`)
- The target app must already be running (the tool reads live cluster state)

## Setup

```bash
uv sync
```

This creates a `.venv/` virtualenv and installs all dependencies. No separate Python install needed — uv manages that too.

## Usage

```bash
uv run python main.py migrate --name <app-name> --namespace <namespace>
uv run python main.py migrate --name <app-name> --namespace <namespace> --context <kubeconfig-context>
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--name` | Name of the App CR (required) |
| `--namespace` | Namespace of the App CR (required) |
| `--context` | kubeconfig context to use (default: current context) |
| `--dry-run` | Stop after showing the generated Flux YAML — no cluster mutations |
| `--output FILE` | Write the generated Flux YAML to a file (independent of `--dry-run`) |
| `--assume-yes` / `-y` | Auto-confirm the proceed and revert-on-failure prompts. Never auto-confirms the destructive delete of App/Chart CRs. |
| `--values-key KIND/NAME=KEY` | Pre-answer the `valuesKey` prompt for a multi-key ConfigMap/Secret (e.g. `ConfigMap/my-cm=values.yaml`). Repeatable. |

**Example — preview before committing:**

```bash
uv run python main.py migrate --name loki --namespace monitoring --dry-run --output loki-flux.yaml
```

**Example — live migration:**

```bash
uv run python main.py migrate --name loki --namespace monitoring
```

**Example — unattended run** (discover the keys interactively once, then replay):

```bash
uv run python main.py migrate --name loki --namespace monitoring \
  --assume-yes --values-key ConfigMap/loki-user-values=values.yaml
```

The tool will:

1. Fetch the App CR and its Catalog CR from the cluster
2. Run preflight checks — including verifying that any `depends-on` dependencies exist as HelmReleases — and print any warnings
3. Resolve `valuesKey` for each ConfigMap/Secret referenced by the app — prompts you to choose when a resource has multiple data keys (or takes the value from `--values-key`; with `--assume-yes` an unresolved multi-key resource is a hard error naming the flag to pass)
4. Show you the generated Flux YAML (and save it if `--output` is set), then ask for confirmation (skipped by `--assume-yes`)
5. Suspend the App CR (and its Chart CR on the workload cluster if applicable)
6. Apply the new Flux resources to the cluster
7. Watch the HelmRelease until it becomes ready — on failure, asks whether to roll back. If any suspend step found the App CR or Chart CR _already_ paused before this run, a note with the matching `kubectl` command is printed so you can undo that state manually if needed
8. Clean up the now-redundant App CR and Chart CR:
   - **Flux-managed app:** prints the reason and kubectl commands to remove finalizers and delete both CRs (Chart CR first). You must commit the generated Flux resources to your gitops repo and remove the App CR from it _before_ running those commands, otherwise the Kustomization will recreate it.
   - **Non-Flux-managed app:** prompts `y/N` to delete both CRs automatically (Chart CR first, App CR second).

## What gets generated

Depending on the Catalog type:

- **OCI catalog** → `OCIRepository` + `HelmRelease`
- **Helm catalog** → `HelmRepository` + `HelmRelease`

Config sources (`spec.config`, `spec.userConfig`, `spec.extraConfigs`) are carried over as `valuesFrom` entries on the HelmRelease, with priorities preserved. Labels and annotations are carried over from the App CR onto every generated resource (HelmRelease and the OCIRepository/HelmRepository), filtering out Flux- and GS-specific keys.

If the App CR carries `app-operator.giantswarm.io/depends-on`, the converted HelmRelease will have a `spec.dependsOn` list. The preflight check verifies that each referenced dependency HelmRelease exists — existence only, not readiness. Flux enforces ordering and waits for dependencies to become Ready at runtime.

## Running tests

```bash
uv run pytest
uv run pytest --cov --cov-report=term-missing
```
