# 12. migrate command: high-level design

Date: 2026-05-29

## Status

Accepted. Implementation to be broken into smaller sessions per phase.

## Context

The `migrate` command is the fourth command in the CLI (see ADR 0008). It drives a
full live migration: fetch the App CR and Catalog CR, convert them, let the user
review the result, then suspend the app platform, apply the Flux resources, monitor
the rollout, and either confirm success or revert.

This ADR captures the high-level design — phase sequence, idempotency strategy, and
cleanup behaviour. Each phase will be detailed in its own session and ADR.

## Decision

### Phase sequence

```
fetch → preflight → convert → confirm → suspend → apply → monitor → (success | revert)
```

1. **Fetch**: reuse `fetcher.fetch()` — same as `fetch-and-convert`.
2. **Preflight**: reuse `run_preflight()` — same as `fetch-and-convert`. Errors exit nonzero; warnings print and continue.
3. **Convert**: reuse `converter.convert()` — produces the Flux YAML.
4. **Confirm**: print the converted Flux YAML and prompt the user. Proceeding past this point triggers live mutations.
5. **Suspend**:
   a. If the App CR is Flux-managed (carries `kustomize.toolkit.fluxcd.io/name` + `kustomize.toolkit.fluxcd.io/namespace` labels), add `kustomize.toolkit.fluxcd.io/reconcile: disabled` to prevent Flux from undoing subsequent patches.
   b. Suspend the App CR (`app-operator.giantswarm.io/paused` annotation).
   c. Suspend the Chart CR (`chart-operator.giantswarm.io/paused` annotation). The Chart CR lives in the `giantswarm` namespace on the MC (in-cluster app) or the WC (remote-cluster app); the client used depends on where the Chart CR lives.
6. **Apply**: apply the HelmRelease and OCIRepository or HelmRepository to the MC.
7. **Monitor**: watch the HelmRelease until it is ready or the user aborts.
8. **Success path**: see "Success cleanup" below.
9. **Revert path**: see "Revert" below.

### Idempotency

Every step is safe to re-run. If `migrate` is interrupted and re-run:

- App CR already suspended → print "already suspended", continue.
- Chart CR already suspended → print "already suspended", continue.
- Flux reconcile label already present → no-op, continue.
- HelmRelease already exists → apply (idempotent), continue to monitor.

No state file is required. The current state of the Kubernetes resources is the source of truth.

### Revert

Triggered when the user aborts during the monitor phase (or chooses to revert on
failure). The user can also choose to skip revert, in which case the tool prints
manual revert steps.

Revert sequence (reverse of suspend/apply):

1. Set `spec.suspend: true` on the HelmRelease.
2. Delete the HelmRelease and the OCIRepository or HelmRepository.
3. Resume the Chart CR (remove `chart-operator.giantswarm.io/paused`).
4. Resume the App CR (remove `app-operator.giantswarm.io/paused`).
5. If a Flux reconcile-disabled label was added in step 5a, remove it.

Revert is also idempotent: if a resource is already in the target state, skip and continue.

### Success cleanup

After the HelmRelease is healthy, the App CR and Chart CR are left suspended.
Cleanup depends on whether the App CR is Flux-managed:

**Flux-managed App CR**: the `reconcile: disabled` label removed the App CR from the
Kustomization inventory. Flux will not prune it even if the manifest is removed from
the gitops codebase. The tool prints instructions:
- Remove the App CR manifest from your gitops codebase.
- Manually delete the App CR and Chart CR from the cluster.

**Non-Flux-managed App CR**: the tool prompts the user:
- "Migration successful. Delete the App CR and Chart CR from the cluster? [y/N]"
- If yes, delete both.

## Consequences

- Each phase is independently re-runnable; interrupted migrations recover by
  re-running `migrate` — no separate recovery command needed.
- The two-client problem (MC + WC) for remote-cluster apps is deferred to the
  implementation session for the suspend/resume phase.
- A potential `revert` command (to revert a completed migration) is out of scope for
  now; the manual steps printed on skip-revert serve as a stopgap.
