# Standalone `cleanup` command

ADR 0021 established that migrating a Flux-managed App CR is a two-phase operation and that the tool can only automate phase one. `migrate` suspends, applies, and monitors, then prints four raw `kubectl` commands and hands phase two — deleting the App CR and Chart CR after the converted resources reach the gitops repository — to the operator. Those commands verify nothing: they will delete an App CR whose HelmRelease never went Ready, is suspended, or was never adopted by gitops. They also clear `finalizers: []` wholesale rather than removing the single entry. And an operator who declined `migrate`'s delete prompt for a non-Flux app, or whose `migrate` died after the monitor step, has no way back into the automated path.

**Decision: add a `cleanup` command that verifies phase one completed, then runs the existing deletion logic.**

`python main.py cleanup --name X --namespace Y [--context ctx] [--dry-run] [-y]`. Same targeting arguments as `migrate`. No `--output` (nothing is serialized) and no `--values-key` (no conversion happens). `--assume-yes` skips the confirmation prompt only, never a check.

It does not go through `fetcher.fetch()`. Fetch also retrieves the Catalog CR — hard-erroring if it is gone — plus every referenced ConfigMap, Secret, and dependency HelmRelease. None of that is needed to delete two objects, and a Catalog CR deleted after migration would otherwise block cleanup for no reason. `cleanup` builds one MC client and GETs the App CR inline; a 404 is an info line and exit 0.

**Verification** lives in `verify_migration(api, app) -> list[str]` in `migrator/cleanup.py`, returning all failure messages at once (empty = pass) so the operator sees every problem in one run. It makes exactly one API call — the HelmRelease GET; everything else is a dict read on the already-fetched App CR.

Always:

1. A HelmRelease exists at the App CR's own `namespace/name` (the converter's naming convention). 404 short-circuits the rest.
2. Its `Ready` condition is `True`.
3. `spec.suspend` is not `true`. `ApplyFluxResources.revert()` suspends before deleting, so a half-completed revert leaves a suspended HelmRelease still reporting `Ready=True`.
4. `status.observedGeneration == metadata.generation`, otherwise `Ready` describes an older revision than the one gitops last pushed.

Checks 3 and 4 skip rather than fail when the fields are absent — a never-reconciled HelmRelease will already have failed check 2.

Only when the App CR is Flux-managed:

5. The HelmRelease carries both `kustomize.toolkit.fluxcd.io/name` and `/namespace`.
6. The App CR carries `kustomize.toolkit.fluxcd.io/reconcile: disabled`.

**Why check 5 is a reliable gitops-adoption detector:** ADR 0017 and ADR 0025 strip every `fluxcd.io/` label from the resources this tool generates, so a HelmRelease applied by `migrate` can never carry the kustomize labels. When Flux applies that same HelmRelease out of the gitops repository, kustomize-controller stamps them on. Their presence is therefore proof the commit landed and was applied — no state file, no marker annotation, cluster state remains the source of truth (ADR 0012).

**Why check 6 reads a label instead of the Kustomization inventory:** `reconcile: disabled` is what evicts an object from `status.inventory.entries`, so the two questions are equivalent, and the label read costs no extra API call. Verified on a live MC: an App CR carrying the label was absent from its Kustomization's inventory while 19 sibling App CRs in the same namespace were present. For anyone who revisits this, inventory entry ids are `{namespace}_{name}_{group}_{Kind}`, with an empty group segment for core types (`..._operations-config__ConfigMap`).

**Non-Flux-managed App CRs run checks 1–4 only and are accepted.** Checks 5 and 6 describe conditions that cannot apply to them.

**Two changes to shared deletion code.** `delete_app_and_chart` now tolerates a 404 on the Chart CR GET — skip the chart block, still delete the App CR. Today it hard-errors, which is a dead end: the function deletes Chart first and App second, so the natural partial-failure state is exactly "Chart gone, App still present", and a re-run can never get past it. It also covers apps that were never installed and so have no Chart CR. `migrate` inherits the fix. A non-404 `ApiException` stays fatal. `flux_cleanup_message` now leads with the `cleanup` invocation and keeps the `kubectl` block beneath it as a manual fallback — the escape hatch if a check ever produces a false positive.

The `is_flux_managed(obj)` predicate moves into `migrator/__init__.py`. It was written out three times (`main.py`'s clean-up branch, `DisableFluxReconcileApp`, and now check 5); consolidating is a net deletion. `migrate`'s `--assume-yes` help text is scoped to `migrate` — its "never the destructive delete of App/Chart CRs" promise (ADR 0023) does not hold for the sibling command.

**Explicit non-goal: `cleanup` never reads the gitops repository** and cannot prove the App CR manifest was removed from it. Nothing readable from the cluster distinguishes "removed from git" from "still in git but suppressed by the reconcile label" — confirmed live, where the app's sibling user-values ConfigMap was still in the inventory, so the gitops directory was demonstrably active. Note also that the Kustomization runs `spec.prune: false`: pruning was never the recreate mechanism, re-application is. Removing the App CR from git deletes nothing, and deleting it by hand while it remains in git gets it re-applied on the next interval. Mitigation is one line in the pre-delete summary ("This does not verify the App CR was removed from your gitops repo. If it is still there, Flux will re-apply it."), not a second prompt — the operator who just made the gitops commit is the wrong person to interrogate about whether they made it.

Remote-cluster apps resolve `chart_api` via `load_wc_client` using the same branch as `migrate`, before the `--dry-run` exit, so a dry run also proves the workload cluster kubeconfig is readable. Both are read-only.
