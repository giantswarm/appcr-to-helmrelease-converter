# 29. `--pull-secret` for authenticated OCI and Helm catalogs

Date: 2026-08-20

## Status

Accepted

## Context

Every source resource the converter emits is anonymous. `build_oci_repository`
and `_build_helm_repository` (`converter/resources.py`) write `spec.url` and
nothing else about access. That is correct for a public catalog and wrong for a
private one: `source-controller` gets a 401 from the registry, the
OCIRepository never becomes `Ready`, and `MonitorHelmRelease` blocks until it
gives up. The App CR has no equivalent field to carry over — app-operator
resolves catalog credentials through machinery that does not survive the
conversion — so the operator must supply the Secret name.

Flux does have the field. Both source kinds take a `spec.secretRef.name`, and
both require the Secret to live in the same namespace as the source resource.
`_filtered_metadata(app)` puts the source resource in `app.metadata.namespace`,
so "same namespace as the App CR" and "same namespace as the source resource"
are the same constraint, and it is Flux's, not ours.

**The two kinds do not accept the same Secret.** From the Flux source API docs:

| Generated resource | Expected Secret | Keys |
|---|---|---|
| `OCIRepository` (Path A) | `kubernetes.io/dockerconfigjson` or `kubernetes.io/dockercfg` — "the same format as `imagePullSecrets`" | `.dockerconfigjson` / `.dockercfg` |
| `HelmRepository`, `type` unset (Path B) | opaque, basic auth | `username` + `password` |

Path B emits a plain HTTP/S HelmRepository — the converter never sets
`spec.type: oci` — so a `docker-registry` Secret does not authenticate it. One
flag therefore points at two incompatible Secret shapes, and an
existence-only check accepts both. The operator would learn about the mismatch
from a `source-controller` log line after `migrate` had already suspended the
App CR.

## Decision

Add `--pull-secret NAME` to `migrate`. It takes a Secret name only, never
`namespace/name`, because Flux allows only the co-located Secret. It is
optional; without it nothing changes.

**Fetch.** `fetcher.fetch()` takes a new `pull_secret: str | None` keyword and
GETs the Secret from the App CR's namespace through the existing `_fetch_one`
helper, which already returns `None` on 404 and raises `FetchError` on anything
else. The result lands on `FetchResult.pull_secret`. No new I/O code — this is
the third consumer of that helper.

**Preflight.** One new check, `check_pull_secret`, reports three findings at
two severities:

| Finding | Severity |
|---|---|
| The Secret does not exist in the App CR's namespace | `PreflightError` |
| The catalog is OCI and the Secret `type` is neither `kubernetes.io/dockerconfigjson` nor `kubernetes.io/dockercfg` | `PreflightWarning` |
| The catalog is Helm-only and the Secret `data` lacks `username` or `password` | `PreflightWarning` |

Absence is an error because there is nothing to reference — Flux would resolve
`secretRef` to a Secret that is not there. Shape is a warning because our model
of "the right shape" is a reading of the Flux docs, not a guarantee.
`source-controller` accepts inputs the table above does not describe, the rules
differ again for a `type: oci` HelmRepository, and they can change between Flux
releases. A check that blocks on a rule we inferred would make this tool wrong
in a way the operator cannot route around; a warning names the expected keys and
leaves the decision with the person who can read the registry's own docs.

`main.py` runs preflight before the `--dry-run` exit and before the confirm
prompt, so the missing-Secret error stops the run before any live mutation,
including under `--dry-run`. The shape warnings print to stderr and the run
continues. Under `--assume-yes` there is no prompt to pause at, so a shape
warning scrolls past in a non-interactive run — the same as every other
`PreflightWarning` already does.

The check needs to know which path the converter will take. `converter` already
answers that question in `_catalog_has_oci`, and `preflight.check_oci_fallback`
re-implements the same two lines. Make it public as
`converter.catalog_has_oci` and call it from both, rather than adding a third
copy.

**Convert.** `convert()` and the two builder pairs take the Secret **name**
only — the converter stays a pure function over dicts and never sees the Secret
body, which preflight has already validated. `build_oci_repository` and
`_build_helm_repository` emit `spec.secretRef: {name: NAME}` when it is set.
It goes between `ref` and `url` on the OCIRepository and between `interval` and
`url` on the HelmRepository, keeping the alphabetical key order both builders
already use. The HelmRelease gets nothing: it pulls the chart through the
source resource, which now carries the credentials.

## Out of scope

- **`certSecretRef` and mutual TLS.** A separate field with a separate Secret
  shape (`tls.crt`, `tls.key`, `ca.crt`). Add a `--cert-secret` flag when a real
  catalog needs one.
- **`spec.provider: aws | azure | gcp`.** Cloud-native registry auth needs no
  Secret at all, and the converter hardcodes `provider: generic`. Orthogonal
  change.
- **`spec.serviceAccountName` on the source resource.** The alternative Flux
  auth path, pulling `imagePullSecrets` off a ServiceAccount. Note that
  `_build_helm_release_common` already sets `spec.serviceAccountName:
  automation` on the *HelmRelease* for ADR 0014 reasons — that is Flux
  impersonation for the Helm install, unrelated to registry auth on the source.
- **Creating or copying the Secret.** The tool checks, it does not provision.
  If the Secret is in the wrong namespace the operator copies it.
- **Whether the Secret exists in the gitops repository.** Handled outside this
  tool.
- **The `cleanup` command.** It converts nothing.

## Consequences

- A private catalog now migrates without hand-editing the generated YAML.
- A missing Secret fails at `migrate` time instead of at Flux reconcile time.
- A wrong-shaped Secret warns at `migrate` time with a message naming the
  expected keys, so the operator sees the likely cause before the 401 rather
  than after it, but keeps the ability to proceed when our reading of the Flux
  rules is the thing that is wrong.
- `fetcher.fetch()` gains a keyword argument and `FetchResult` a field; both
  default to `None`, so `cleanup` and the existing tests are unaffected.
- `convert()` gains a fourth optional parameter.
- `_catalog_has_oci` becomes `catalog_has_oci` with one caller replaced in
  `preflight`, removing a duplicated predicate.
- Tracked as feature-parity backlog item 23 in `CLAUDE.md`.
