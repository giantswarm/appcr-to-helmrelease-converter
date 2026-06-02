# 13. Class-based migration step framework

Date: 2026-06-02

## Status

Accepted

## Context

The `migrate` command drives a live migration through a fixed sequence of phases:
suspend App CR → suspend Chart CR → apply Flux resources → monitor HelmRelease.
Each phase can fail, and on failure (or user abort) the completed phases must be
undone in reverse order.

An earlier draft used standalone functions (`suspend_app`, `resume_app`). As more
phases are added, the revert logic becomes ad-hoc — the command must track which
phases ran and call the right undo function for each one, with increasing branching
as the sequence grows.

## Decision

Each migration phase is a class that implements a `MigrationStep` interface:

```python
class MigrationStep(ABC):
    @property
    @abstractmethod
    def description(self) -> str: ...   # one-line display string

    @abstractmethod
    def apply(self) -> None: ...        # forward action; idempotent; raises MigratorError

    @abstractmethod
    def revert(self) -> None: ...       # undo what apply() changed; raises MigratorError
```

A `MigrationRunner` owns a LIFO stack. Steps are pushed only after `apply()`
succeeds. `revert_all()` pops and calls `revert()` in last-in-first-out order,
giving automatic reverse-sequence rollback without the command knowing the undo order.

```python
class MigrationRunner:
    def run(self, step: MigrationStep) -> None:
        step.apply()
        self._stack.append(step)   # pushed only on success

    def revert_all(self) -> None:
        # pops in LIFO; continues through all steps even if one raises;
        # collects errors and raises a single MigratorError at the end
```

`main.py` calls `runner.run(step)` for each phase and prints `step.description`
before each call. All K8s mutation logic lives inside the step classes.

### Data passed in at construction; no re-fetch in apply()

The `migrate` command fetches the App CR and Catalog CR once at startup via
`fetcher.fetch()`, before the confirm prompt. Steps that operate on these resources
(e.g. `SuspendApp`) receive the already-fetched dict at construction time and read
current state from it directly — no re-fetch inside `apply()`.

Steps that create new resources (HelmRelease, OCIRepository, HelmRepository) deal
with a different situation since those resources do not exist at fetch time.
Idempotency for the Apply step is left to its own implementation session.

### Internal flags for revert

Each step records what it actually changed using private boolean flags
(e.g. `_did_pause`, `_did_disable_reconcile`). `revert()` uses those flags to
undo only what was changed. This keeps the revert path simple and fully testable
without additional cluster reads.

### API client injection

The Kubernetes `CustomObjectsApi` client is created once by `main.py` after the
user confirms, using `migrator.load_client(context)`, and injected into each step
constructor. Alternatives considered:

- **Per-step client creation**: each step calls `load_kube_config` itself (the
  original `suspend_app` pattern). Rejected: kubeconfig is loaded once per step
  (wasteful); tests must always patch `kubernetes.config` even for unit tests.
- **Runner creates the client**: cleaner than per-step, but couples the runner to
  kubeconfig concerns. Rejected: `main.py` already handles all I/O setup.
- **Inject the client** (chosen): kubeconfig loaded exactly once; step tests pass a
  `MagicMock()` directly with no patching required.

### `revert_all()` continues through errors

If one step's `revert()` raises, `revert_all()` continues and attempts all
remaining reverts. All errors are collected and raised as a single `MigratorError`
at the end. Failing fast would leave earlier steps un-reverted with no indication
that further revert steps were skipped.

## Consequences

- Future phases (SuspendChartCR, Apply, Monitor) each add one class implementing
  `MigrationStep`. No changes to the runner or `main.py` call site beyond adding
  `runner.run(new_step)`.
- The standalone `suspend_app()` and `resume_app()` functions are removed; their
  logic moves into `SuspendApp.apply()` and `SuspendApp.revert()`.
- Step unit tests inject a `MagicMock()` API directly — no kubeconfig patching
  needed — which keeps test setup minimal.
- `revert_all()` error accumulation means a caller that ignores its raised
  `MigratorError` could miss revert failures. `main.py` always prints the error and
  advises manual steps.
