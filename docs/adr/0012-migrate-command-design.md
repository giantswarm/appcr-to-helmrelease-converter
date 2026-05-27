# 12. migrate command design

Date: 2026-05-22

## Status

Draft — in progress. Several sub-decisions are still open (marked below).

## Context

The `migrate` command drives a full live migration: fetch → preflight → convert →
suspend operators → apply Flux resources → monitor HelmRelease → revert or confirm.
It sits above `fetch-and-convert` in capability level (see ADR 0008) and is the
only command that writes to the cluster.

## Decisions made

### Scope

`migrate` handles both in-cluster and remote-cluster apps. The HelmRelease and its
source resource always land on the MC regardless of app type, so `--context`
selects the MC and that is the only kubeconfig context the user provides.

For remote-cluster apps, `migrate` additionally needs a Kubernetes client
targeting the WC — but that client is built internally from the kubeconfig Secret
on the MC (see "WC client construction" below), not from a second `--context` flag.

### Cluster clients

- **MC client**: built from `--context` (or the current context if absent). Used
  for everything except Chart CR suspension on remote-cluster apps.
- **WC client** (remote-cluster apps only): built by fetching the kubeconfig
  Secret referenced in `spec.kubeConfig.secret` from the MC. Secret namespace =
  `spec.kubeConfig.secret.namespace` (equals `app.metadata.namespace` after the
  existing preflight check from ADR 0005). Secret key = `value` (CAPI convention,
  Flux default — see ADR 0005). Used only to annotate / remove annotation on the
  Chart CR on the WC.

### Chart CR location and name derivation

- **Namespace**: always `giantswarm`, on whichever cluster the Chart CR lives.
- **Cluster** (in-cluster app): Chart CR is on the MC.
- **Cluster** (remote-cluster app): Chart CR is on the WC.
- **Name**: derived from the App CR name using the same logic as app-operator:

  ```python
  chart_name = app_name.removeprefix(f"{cluster_id}-")
  chart_name = chart_name.removesuffix(f"-{cluster_id}")
  ```

  Both operations are applied in sequence. `cluster_id` comes from the
  `giantswarm.io/cluster` label on the App CR (see "Preflight checks" below).

  For in-cluster apps, the label is absent and no stripping is applied — the
  Chart CR name equals the App CR name.

### Preflight checks (migrate-specific)

A new `check_cluster_label` check is added, called only by `migrate` (not added
to the shared `_CHECKS` list used by `convert` and `fetch-and-convert`):

- If `spec.kubeConfig.inCluster` is `false` (remote-cluster app) **and** the
  `giantswarm.io/cluster` label is absent on the App CR → `PreflightError`.

If the Chart CR is not found on the expected cluster → hard error (abort).

### Suspension

Both operators are annotated before Flux resources are applied:

- `app-operator.giantswarm.io/paused: "true"` on the App CR (MC)
- `chart-operator.giantswarm.io/paused: "true"` on the Chart CR (MC or WC)

Suspension is fire-and-forget — no wait for operator acknowledgment. app-operator
and chart-operator are not continuously drift-detecting; once a reconcile cycle
completes, they do not re-reconcile until something changes.

### Apply

Flux resources are created via `create_namespaced_custom_object`. If any resource
already exists (409 Conflict) → hard error, abort. Overwriting pre-existing Flux
resources could silently take ownership of a release already under management.

### Monitoring flags

| Flag | Default | Notes |
|---|---|---|
| `--timeout` | `10m` | Matches the hardcoded `spec.timeout` on the HelmRelease |
| `--poll-interval` | `10s` | |

`migrate` prints a status line on every poll tick.

### HelmRelease status model

| Condition | Interpretation |
|---|---|
| `Ready: True` | Success — migration complete |
| `Ready: False` + terminal reason (e.g. `InstallFailed`, `UpgradeFailed`) | Failure — trigger revert/leave prompt |
| Anything else | Progressing — keep polling |

Timeout (exhausted `--timeout`) is treated the same as failure: the revert/leave
prompt is shown with a "timed out after Xm" message.

> **Deferred**: HelmRelease has `spec.install.remediation.retries: 10`. If
> `migrate` times out while the HR is still retrying, cleanly stopping the HR
> before revert needs a dedicated design. Not resolved yet.

### Interactivity

`migrate` is interactive by default, with prompts at two points:

1. **Before suspension** — "About to suspend app-operator and chart-operator on
   app X. Proceed? [y/N]"
2. **On failure or timeout** — "HelmRelease failed. Revert (delete Flux resources,
   unsuspend operators) or leave for inspection? [r/l]"

If the user chooses **leave**: `migrate` prints the kubectl commands needed to
manually revert (remove annotations, delete Flux resources) and exits nonzero.

`--yes` flag: skips the pre-suspension confirmation and auto-reverts on failure.

### Revert failure handling

If any revert step fails (delete HelmRelease, delete source resource, remove
annotation from App CR, remove annotation from Chart CR), `migrate` continues
through the remaining steps and reports all failures at the end. A partial revert
is better than a stalled one — leaving both operators suspended is worse than
surfacing multiple errors.

## Open items

- [ ] Exact terminal `reason` values on HelmRelease conditions that constitute
      "failure" vs "still progressing" — needs verification against Flux source.
- [ ] How to stop a running HelmRelease cleanly before reverting on timeout (HR
      retries complicate this — deferred).
- [ ] Exact kubectl commands to print in the "leave for inspection" remediation
      message.
- [ ] What `migrate` prints on success.

## Consequences

- `migrate` is the only command that mutates the cluster; all other commands are
  read-only or emit YAML only.
- Interactive-by-default makes it unsuitable for unattended scripting without
  `--yes`.
- The WC client is constructed internally from the kubeconfig Secret, so the user
  never needs to supply a second `--context` — the same MC context covers the
  whole migration.
- The Chart CR name derivation mirrors app-operator exactly; any future change to
  app-operator's naming convention would require updating `migrate` in sync.
