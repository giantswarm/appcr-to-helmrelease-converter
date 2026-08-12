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
| `--assume-yes` / `-y` | Auto-confirm the proceed and revert-on-failure prompts. For `migrate`, never auto-confirms the destructive delete of App/Chart CRs — see the `cleanup` command below, where `-y` does skip that prompt. |
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
3. Resolve `valuesKey` for each ConfigMap/Secret referenced by the app — prompts you to choose when a resource has multiple data keys (or takes the value from `--values-key`; with `--assume-yes` an unresolved multi-key resource is a hard error naming the flag to pass). A resource with zero data keys is never an error: it's carried into `valuesFrom` as `optional: true` with a warning, instead
4. Show you the generated Flux YAML (and save it if `--output` is set), then ask for confirmation (skipped by `--assume-yes`)
5. Suspend the App CR (and its Chart CR on the workload cluster if applicable)
6. Apply the new Flux resources to the cluster
7. Watch the HelmRelease until it becomes ready — on failure, asks whether to roll back. If any suspend step found the App CR or Chart CR _already_ paused before this run, a note with the matching `kubectl` command is printed so you can undo that state manually if needed
8. Clean up the now-redundant App CR and Chart CR:
   - **Flux-managed app:** prints the exact `uv run python main.py cleanup ...` command to run — the recommended path, once you've committed the generated Flux resources to your gitops repo and removed the App CR from it. The kubectl commands to remove finalizers and delete both CRs (Chart CR first) are still printed underneath as a manual fallback.
   - **Non-Flux-managed app:** prompts `y/N` to delete both CRs automatically (Chart CR first, App CR second).

### `cleanup`

```bash
uv run python main.py cleanup --name <app-name> --namespace <namespace>
uv run python main.py cleanup --name <app-name> --namespace <namespace> --dry-run
```

Phase two of the migration: run once the converted resources have been committed to your gitops repository and the App CR removed from it. Verifies the migration actually completed and that Flux has adopted the HelmRelease, then deletes the now-redundant App CR and Chart CR.

**Flags:**

| Flag | Description |
|------|-------------|
| `--name` | Name of the App CR (required) |
| `--namespace` | Namespace of the App CR (required) |
| `--context` | kubeconfig context to use (default: current context) |
| `--dry-run` | Run every check and stop — no deletions |
| `--assume-yes` / `-y` | Auto-confirm the deletion prompt. Never skips a check. |

**What it checks:**

1. A HelmRelease exists at the App CR's own name/namespace
2. The HelmRelease is `Ready`
3. The HelmRelease is not suspended
4. The HelmRelease's observed generation matches its current generation (not describing a stale revision)
5. _(Flux-managed App CRs only)_ The HelmRelease carries Flux's kustomize labels — proof gitops has adopted it
6. _(Flux-managed App CRs only)_ The App CR has Flux reconciliation disabled — proof Flux won't recreate it once deleted

If any check fails, `cleanup` reports every failure and exits non-zero without deleting anything.

For a remote-cluster app, `cleanup` also resolves the workload-cluster kubeconfig (same as `migrate`) to delete the Chart CR there — a `--dry-run` therefore also proves that kubeconfig is reachable.

**`cleanup` never reads your gitops repository and cannot prove the App CR manifest was actually removed from it.** If it is still there, Flux re-applies the App CR after `cleanup` deletes it.

### `suspend`

```bash
uv run python main.py suspend --name <app-name> --namespace <namespace>
uv run python main.py suspend --name <app-name> --namespace <namespace> --context <kubeconfig-context>
```

Standalone utility, not a migration phase: pauses app-operator/chart-operator reconciliation on the App CR and its Chart CR — the same two paused annotations `migrate` sets as part of its own Suspend step — without running any of `migrate`'s conversion, apply, or monitor logic. Useful when you want reconciliation paused ahead of time, independent of running a full migration through this tool.

**Flags:**

| Flag | Description |
|------|-------------|
| `--name` | Name of the App CR (required) |
| `--namespace` | Namespace of the App CR (required) |
| `--context` | kubeconfig context to use (default: current context) |

No `--dry-run` or `--assume-yes`: both steps just set an annotation if it isn't already set, so there's nothing destructive to preview or confirm. A failed run can simply be re-run once the underlying issue (e.g. a permissions error) is fixed.

**What it does:**

1. Annotates the App CR with `app-operator.giantswarm.io/paused: "true"` (skipped if already set)
2. Annotates the Chart CR with `chart-operator.giantswarm.io/paused: "true"` (skipped if already set, or if the Chart CR doesn't exist — an app that was never installed has nothing to pause)

If the App CR itself isn't found, `suspend` exits cleanly with nothing to do. For a remote-cluster app, it resolves the workload-cluster kubeconfig the same way `migrate`/`cleanup` do.

### `resume`

```bash
uv run python main.py resume --name <app-name> --namespace <namespace>
uv run python main.py resume --name <app-name> --namespace <namespace> --context <kubeconfig-context>
```

The inverse of `suspend`: clears the same two paused annotations instead of setting them, so app-operator/chart-operator reconciliation resumes. Same standalone scope as `suspend` — no conversion, apply, or monitor logic runs.

**Flags:**

| Flag | Description |
|------|-------------|
| `--name` | Name of the App CR (required) |
| `--namespace` | Namespace of the App CR (required) |
| `--context` | kubeconfig context to use (default: current context) |

No `--dry-run` or `--assume-yes`, for the same reason as `suspend`: clearing an annotation is idempotent and non-destructive.

**What it does:**

1. Clears `app-operator.giantswarm.io/paused` from the App CR
2. Clears `chart-operator.giantswarm.io/paused` from the Chart CR (skipped if the Chart CR doesn't exist — nothing to resume)

Unlike `suspend`, `resume` doesn't check current annotation state before acting — clearing an annotation that's already absent is a harmless no-op — so it reports the same outcome whether or not anything was actually paused to begin with.

If the App CR itself isn't found, `resume` exits cleanly with nothing to do. For a remote-cluster app, it resolves the workload-cluster kubeconfig the same way `migrate`/`cleanup`/`suspend` do.

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
