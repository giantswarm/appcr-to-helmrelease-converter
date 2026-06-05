# Set serviceAccountName on HelmReleases outside excluded namespaces

The `flux-multi-tenancy` Kyverno ClusterPolicy (enforced on all management clusters) requires every HelmRelease outside the `giantswarm`, `flux-giantswarm`, and `monitoring` namespaces to have either `spec.serviceAccountName` or `spec.kubeConfig.secretRef.name` set. Remote-cluster apps already satisfy the rule via `kubeConfig`. For in-cluster apps in org namespaces (e.g. `org-acme`), we set `spec.serviceAccountName: automation` — the service account Flux uses for tenant reconciliation in those namespaces.
