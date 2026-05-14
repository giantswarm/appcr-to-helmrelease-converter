# 4. spec.namespaceConfig is dropped with a preflight warning

Date: 2026-05-14

## Status

Accepted

## Context

App CRs may carry `spec.namespaceConfig` — a map of annotations and labels that
app-operator applies to the target namespace when it creates or manages it. There
is no equivalent field on a HelmRelease.

This converter is a migration tool: by the time it is run, the cluster already
exists and the target namespace already exists with its labels and annotations in
place (applied previously by app-operator). There is nothing to migrate.

## Decision

### Behaviour

The converter drops `spec.namespaceConfig` without affecting the output YAML.
When the field is present, the CLI emits a warning to stderr so the operator is
aware that config was encountered and intentionally not carried over:

```
warning: spec.namespaceConfig dropped (no HelmRelease equivalent; target namespace already exists)
```

Conversion continues and exits zero even when the warning is emitted.

### Implementation

Pre-flight validation lives in a dedicated `preflight/` package, separate from
`converter/`. The package exposes:

- `PreflightWarning(Exception)` — non-fatal; conversion continues after printing
- `PreflightError(Exception)` — fatal; conversion halts with a non-zero exit code
- Individual check functions (e.g. `check_namespace_config`) that raise one of
  the above when a problem is detected
- `run_preflight(app: dict) -> list` — calls all registered checks, collects all
  issues (both warnings and errors), and returns them

`main.py` calls `run_preflight`, prints each issue prefixed with its severity,
then exits non-zero if any `PreflightError` was returned.

This separation keeps `converter/` as pure functions (see ADR 0001), keeps I/O
in `main.py`, and makes the checks unit-testable without capturing stderr —
tests assert on raised exception types directly.

## Consequences

- Conversion succeeds and produces valid output when only warnings are present.
- The operator sees all warnings and errors before a fatal halt (no silent drops).
- New pre-flight checks slot into `preflight/` without touching `main.py` logic.
- Individual check functions are tested by asserting on raised exception types,
  without needing CLI invocation or stderr capture.
