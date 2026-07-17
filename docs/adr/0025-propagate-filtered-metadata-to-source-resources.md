# 25. Propagate filtered App CR labels/annotations to OCIRepository and HelmRepository

Date: 2026-07-17

## Status

Accepted

## Context

ADR 0017 established that App CR labels and annotations are copied onto the
generated HelmRelease, minus an exact blocklist (`_ANNOTATION_BLOCKLIST` /
`_LABEL_BLOCKLIST`) and any key containing `fluxcd.io/` (substring match, to
drop Flux-controller-owned keys regardless of which controller domain stamped
them). That ADR is scoped explicitly to "the generated HelmRelease" — the
OCIRepository and HelmRepository source resources produced alongside it only
ever got a bare `metadata.name` / `metadata.namespace`, with no label or
annotation propagation at all.

There is no reason for that asymmetry. Labels and annotations on the App CR
(team ownership, cost-center, environment tags, etc.) are equally meaningful
on the source resources; anything already judged unsafe or meaningless to
copy onto the HelmRelease (per the ADR 0017 blocklist and `fluxcd.io/` filter)
is equally unsafe or meaningless on an OCIRepository or HelmRepository.

## Decision

`build_oci_repository` and `_build_helm_repository` (`converter/resources.py`)
now apply the exact same filtered label/annotation copy as the HelmRelease:
same `_ANNOTATION_BLOCKLIST` / `_LABEL_BLOCKLIST` constants, same `fluxcd.io/`
substring drop, same "only add the key when the filtered dict is non-empty"
behavior. The filter rules themselves are unchanged from ADR 0017 — only
their reach is extended to the other two resource kinds. Implemented via a
shared `_filtered_metadata(app)` helper used by all three builders, so the
filter logic exists in exactly one place instead of being copy-pasted three
times.

One HelmRelease-specific behavior does NOT extend to the other two
resources: `app-operator.giantswarm.io/depends-on` is still consumed into
`spec.dependsOn` only on the HelmRelease (see ADR 0020) — OCIRepository and
HelmRepository have no equivalent field. The annotation is still dropped
from their metadata too, same as any other blocklisted key; it's just never
*read* by those two builders.

## Consequences

- OCIRepository and HelmRepository now carry the same filtered
  team/ownership/cost labels and annotations as the HelmRelease they're
  paired with, instead of bare `name`/`namespace` metadata.
- Any future change to the blocklist or the `fluxcd.io/` filter automatically
  applies to all three generated resources, since they share one helper.
- Implemented as feature-parity backlog item 19 in `CLAUDE.md`.
