# 28. Fix cluster-prefix stripping predicate for releaseName and Chart CR name

Date: 2026-08-18

## Problem

`_release_name()` (`converter/resources.py`) and `chart_cr_name()` (`migrator/__init__.py`) both strip the `giantswarm.io/cluster` label value as a prefix/suffix from the App CR's `metadata.name`:

```python
if cluster_id:
    name = name.removeprefix(f"{cluster_id}-")
    name = name.removesuffix(f"-{cluster_id}")
```

Both were written on the same assumption, stated in commit 29888ff ("Fix releaseName: strip cluster name for WC-targeted HelmReleases") and codified in CONTEXT.md's Chart CR entry: **only remote-cluster (WC-targeted) apps carry the `giantswarm.io/cluster` label; in-cluster apps never do.** Under that assumption, label presence is a safe proxy for "strip the prefix."

The assumption is false. Reported in [issue #12](https://github.com/giantswarm/appcr-to-helmrelease-converter/issues/12): App CR `operations-auth-bundle` (namespace `org-giantswarm-production`, MC `gazelle`, `kubeConfig.inCluster: true`) carries `giantswarm.io/cluster: operations` — it's an in-cluster, per-workload-cluster component app sharing a namespace with sibling apps for the same WC (`operations-app-operator`, `operations-karpenter-bundle`, etc.). Its real Helm release name, confirmed via `helm list -n org-giantswarm-production`, is the **unstripped** `operations-auth-bundle`. Every sibling app in that namespace follows the same pattern.

So the label marks "this app is scoped to workload cluster X," which is orthogonal to whether the release name convention strips the cluster prefix. The convention actually tracks the **namespace pattern**: a dedicated per-app namespace on a remote WC reliably strips; a shared MC-local namespace does not.

## Impact

Two call sites share the flawed predicate, with two different failure modes:

- **`_release_name()`** — `migrate` emits a `HelmRelease.spec.releaseName` that doesn't match the existing Helm release. `helm-controller` would attempt a fresh install under the wrong name, in a namespace where objects owned by the real release already exist. This is the symptom the issue reports (`--dry-run` output).
- **`chart_cr_name()`** — used by `SuspendChart` (`migrator/suspend_chart.py`) and by `cleanup` (`migrator/cleanup.py`) to `GET` the Chart CR by computed name. For this same class of app, the computed name doesn't exist; `SuspendChart.apply()` hard-errors with `MigratorError` on the 404. This path isn't exercised by `--dry-run` (which stops before live steps — ADR 0019), so it wasn't visible in the reporter's canary run, but a live `migrate` against one of these apps would fail here before ever reaching the `releaseName` bug. `cleanup` would fail the same way if it ever got this far.

Net effect: `migrate` cannot currently complete against any in-cluster, shared-namespace, cluster-scoped App CR.

## Decision

Fix both call sites together, from one corrected predicate, rather than patching `_release_name()` alone — patching only the reported symptom would still leave `SuspendChart` 404ing for the exact apps this issue is about.

Replace the label-presence check with the condition the code already uses elsewhere to distinguish these two namespace patterns: `spec.kubeConfig.inCluster is False` (remote). `_build_helm_release_common()` already computes this as `is_remote` for the `spec.kubeConfig` block; `SuspendChart` already branches on it for the revert-note WC hint. Strip the cluster prefix only when `is_remote` — i.e. only for the dedicated-per-app-namespace-on-a-remote-WC pattern the original stripping logic was written for.

Consolidate `_release_name()` and `chart_cr_name()` into a single implementation, living in `converter/resources.py` as the public `release_name(app)`. `converter` has no dependencies of its own (no `kubernetes` import); `migrator` already depends on `kubernetes`, so `migrator/__init__.py` importing from `converter` doesn't create a cycle and doesn't pull anything new into the pure package — the reverse direction (converter importing migrator) would have. `migrator.chart_cr_name` becomes `from converter.resources import release_name as chart_cr_name`, a plain alias — every existing caller (`suspend_chart.py`, `cleanup.py`, `main.py`) keeps importing `chart_cr_name` from `migrator` unchanged.

**Resolved:** no `--release-name` CLI override flag added. The corrected heuristic (`is_remote`, already used elsewhere in both files) covers every known namespace pattern; a flag would be speculative until a pattern is found that breaks it too.

## Also fixed

CONTEXT.md's Chart CR entry claimed "For in-cluster apps the label is absent and no stripping is applied" — disproven by the evidence above. Corrected to describe the actual predicate (namespace pattern / `inCluster`, not label presence).
