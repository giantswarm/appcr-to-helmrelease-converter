# 33. Migrate only Deployed App CRs

Date: 2026-10-01

## Status

Accepted

## Context

`migrate` never looked at the App CR's status. It would convert and hand to
Flux an App CR whose Helm release had failed, was mid-upgrade, or had never
been installed. Flux then adopts a release in an unknown state, and a failure
in `MonitorHelmRelease` cannot tell an old problem from one the migration
caused. For Release chart substitution (ADR 0032) the risk is concrete: after a
failed upgrade the Cluster CR's version label and the App CR's `spec.version`
describe different releases.

## Decision

Preflight requires a **Deployed App CR**: `status.release.status` is `deployed`
**and** `status.version` equals `spec.version`. Anything else is a hard
preflight error showing both values. The second condition catches an upgrade
that was requested but never landed. App CRs carry no `Ready` condition, so
this pair is the app-operator's own readiness signal.

The rule applies to every migration, not only to Cluster chart apps.

`--ignore-app-status` on `migrate` turns the error into a warning that still
shows the ignored status, restoring the previous behaviour for an operator who
needs it. It skips only this check — none of the Release chart substitution
checks in ADR 0032 — and `--assume-yes` does not imply it.

## Consequences

- A migration that used to start against an unhealthy App CR now stops before
  any mutation, under `--dry-run` too.
- Fleet-wide scripted runs skip unhealthy apps by failing on them, rather than
  migrating them silently.
- `cleanup`, `suspend` and `resume` are unchanged: `cleanup` judges the
  HelmRelease, not the App CR.
