# Revert note for pre-existing suspend state

When a `MigrationStep.revert()` is a no-op because the step's change was never made by this run — most commonly because the App CR or Chart CR was already in the target state when the migration started — the operator is left with no indication that those objects remain in their suspended state. This matters on a rerun: the first partial run may have paused the App CR and Chart CR, the operator reruns, a later step fails, and revert silently skips the suspend steps because `_did_pause` is `False` on the new runner instance.

**Decision: each step that can silently skip its revert emits a note with a kubectl command.**

`MigrationStep` gains an optional `revert_note: str | None` property (default `None`). Steps that skip their revert because the relevant state was already present at apply time set an internal `_revert_note` string and expose it via this property. `MigrationRunner.revert_all()` collects non-`None` notes into `self.revert_notes: list[str]` as it unwinds the stack. `main.py` prints all collected notes to stderr after the revert block (whether revert succeeded or partially failed).

**Which steps emit notes:**

| Step | Condition | kubectl command |
|---|---|---|
| `SuspendApp` | App CR was already annotated `app-operator.giantswarm.io/paused: "true"` at apply time | `kubectl annotate app <name> -n <ns> app-operator.giantswarm.io/paused-` |
| `SuspendChart` | Chart CR was already annotated `chart-operator.giantswarm.io/paused: "true"` at apply time | `kubectl annotate chart <name> -n giantswarm chart-operator.giantswarm.io/paused-` — when `spec.kubeConfig.inCluster: false`, the note also includes the MC Secret name/namespace from which to obtain the WC kubeconfig |
| `DisableFluxReconcileApp` | App CR is Flux-managed and already carried `kustomize.toolkit.fluxcd.io/reconcile: disabled` at apply time | `kubectl label app <name> -n <ns> kustomize.toolkit.fluxcd.io/reconcile-` |

`DisableFluxReconcileApp` skips silently on non-Flux apps (entirely inapplicable — no note emitted). The `_was_already_paused` / `_was_already_disabled` flag is set in `apply()` when the idempotency guard fires, so `revert()` can distinguish "skipped because pre-existing" from "skipped because inapplicable."

**Why not always revert regardless of `_did_pause`:** revert unconditionally would unpause an App CR that was suspended before the migration started for an unrelated reason. The `_did_pause` semantics are intentional; the gap is only the communication when a rerun's new runner instance loses that history.
