# depends-on annotations map to spec.dependsOn; preflight checks existence only

App CRs carry `app-operator.giantswarm.io/depends-on` (comma-separated dependency names) and optionally `app-operator.giantswarm.io/depends-on-helmrelease` (a flag telling app-operator to resolve those names as HelmReleases rather than App CRs). After migration every dependency is a HelmRelease, so both annotations collapse to a single `spec.dependsOn` list on the generated HelmRelease — no name translation needed, and the flag carries no meaning in the Flux world.

A preflight check verifies that each referenced dependency HelmRelease exists on the cluster but does not require `Ready=True`. Requiring readiness would block migrations during partial rollouts where a dependency is installed but temporarily unhealthy. Flux already enforces ordering at runtime: it will not reconcile a HelmRelease until all its `spec.dependsOn` entries are Ready.
