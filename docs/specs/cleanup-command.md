# Spec: `cleanup` command

## Problem Statement

Migrating a Flux-managed App CR is a two-phase operation, and the tool only automates the
first phase.

`migrate` performs the live migration up to the point where it becomes unsafe to continue:
it suspends the App CR and Chart CR, applies the converted Flux resources, and waits for
the HelmRelease to become ready. It then stops. It cannot delete the now-redundant App CR
and Chart CR, because the operator must first commit the converted resources to the gitops
repository and remove the App CR from it. Deleting beforehand gets the App CR re-applied by
its Kustomization, after which app-operator resumes and fights the HelmRelease over the
same Helm release.

So `migrate` prints four raw `kubectl` commands and hands phase two to the human. Those
commands verify nothing. They will delete an App CR whose HelmRelease never became ready,
is suspended, or was never adopted by gitops at all. They also clear the finalizer list
wholesale rather than removing the single entry they mean to remove. An operator who runs
them a day later, from shell history, against the wrong app, gets no warning.

There is also no way back into the automated path. An operator who declined `migrate`'s
delete prompt for a non-Flux-managed app, or whose `migrate` died after the monitor step,
has only kubectl. And because the existing deletion routine treats a missing Chart CR as a
hard error, a deletion interrupted between the Chart CR and the App CR can never be
completed by re-running — that is precisely the state a partial failure leaves behind.

## Solution

A second command, `cleanup`, taking the same targeting arguments as `migrate`. It is phase
two of the live migration: it proves phase one actually completed and that gitops has taken
ownership of the HelmRelease, then performs the same deletion `migrate` already knows how
to do.

The operator's workflow becomes:

1. `migrate` — suspend, convert, apply, monitor. Ends by naming the `cleanup` command to
   run once gitops is updated, with the manual `kubectl` commands retained underneath as a
   fallback.
2. Commit the converted resources to the gitops repository, remove the App CR from it, let
   Flux apply.
3. `cleanup` — verify, then delete the Chart CR and App CR.

The verification rests on a property the converter already guarantees: every `fluxcd.io/`
label is stripped from the resources this tool generates. A HelmRelease applied by the tool
therefore never carries `kustomize.toolkit.fluxcd.io/name`. When Flux later applies that
same HelmRelease from the gitops repository, Flux stamps the label on. Its presence is
proof that the gitops commit landed and was applied — with no state file, no marker
annotation, and no reading of the git repository. Cluster state remains the source of
truth.

## User Stories

1. As a platform operator, I want a command that deletes the App CR and Chart CR after a
   migration, so that I do not have to run raw `kubectl` commands from my shell history.
2. As a platform operator, I want the tool to refuse to delete anything unless a
   HelmRelease exists for the app, so that I cannot destroy an App CR for a migration that
   never ran.
3. As a platform operator, I want the tool to refuse unless that HelmRelease is ready, so
   that I do not delete the old management path while the new one is still failing.
4. As a platform operator, I want the tool to refuse if the HelmRelease is suspended, so
   that I do not end up with a workload that nothing at all is reconciling.
5. As a platform operator, I want the tool to refuse if the HelmRelease's observed
   generation lags its metadata generation, so that I am not misled by a ready condition
   describing an older revision than the one gitops last pushed.
6. As a platform operator migrating a Flux-managed app, I want the tool to confirm that
   Flux has adopted the HelmRelease from my gitops repository before deleting anything, so
   that I cannot delete the App CR while the converted resources exist only on the cluster.
7. As a platform operator, I want that adoption check to be based on what Flux itself
   stamps onto the object, so that it cannot be fooled by resources this tool applied
   moments earlier.
8. As a platform operator, I want the tool to refuse if the App CR is still being
   reconciled by its Kustomization, so that I do not delete an object Flux will immediately
   re-apply.
9. As a platform operator, I want every failed check reported in one run, so that I fix all
   of them at once instead of rediscovering them one command at a time.
10. As a platform operator, I want each failure message to name the specific object and
    condition that failed, so that I know what to fix without reaching for `kubectl`.
11. As a platform operator, I want a dry-run mode that runs every check and stops, so that
    I can ask "is this app ready for cleanup?" without any risk of deleting something.
12. As a platform operator managing a fleet, I want a non-interactive mode, so that I can
    clean up many migrated apps in a scripted sweep.
13. As a platform operator, I want the non-interactive flag to skip only the confirmation
    prompt and never a safety check, so that scripting cannot silently weaken the
    guarantees.
14. As a platform operator, I want to see exactly which objects will be deleted before
    confirming, so that I can catch a mistargeted invocation.
15. As a platform operator cleaning up a Flux-managed app, I want to be reminded that the
    tool cannot see my gitops repository, so that I understand which part of the safety
    argument is still mine to uphold.
16. As a platform operator, I want to run `cleanup` against an app whose App CR is already
    gone and have it exit successfully, so that re-running it over a batch is safe.
17. As a platform operator whose previous cleanup died after deleting the Chart CR, I want
    a re-run to finish the job, so that I am not forced into manual recovery.
18. As a platform operator, I want the same tolerance to apply inside `migrate`, so that
    the identical dead end cannot occur there either.
19. As a platform operator migrating an app that was never installed and so has no Chart
    CR, I want cleanup to delete the App CR anyway, so that a missing Chart CR does not
    block me.
20. As a platform operator working with a remote-cluster app, I want the Chart CR deleted
    on the workload cluster automatically, so that I do not have to switch kubeconfigs by
    hand.
21. As a platform operator, I want a failure to reach the workload cluster to stop the run
    before anything is deleted, so that I am not left with an App CR gone and an orphaned
    Chart CR I cannot reach.
22. As a platform operator, I want the Chart CR deleted before the App CR, so that
    app-operator does not recreate the Chart CR from an App CR that still exists.
23. As a platform operator, I want the suspension annotations re-applied if they went
    missing before deletion proceeds, so that neither operator acts on the objects while
    they are being removed.
24. As a platform operator who declined `migrate`'s delete prompt for a non-Flux-managed
    app, I want `cleanup` to accept that app too, so that I have a way back into the
    automated path.
25. As a platform operator working on a non-Flux-managed app, I want the gitops-specific
    checks skipped, so that I am not blocked by conditions that cannot apply to me.
26. As a platform operator, I want `migrate` to tell me the exact `cleanup` command to run
    next, so that I do not have to reconstruct the arguments later.
27. As a platform operator, I want `migrate` to keep printing the manual `kubectl`
    commands, so that I retain an escape hatch if a check is ever wrong or the tool is
    unavailable.
28. As a platform operator, I want partial deletion failures reported with what succeeded
    and what did not, so that I know the precise remaining state.
29. As a platform operator, I want a non-zero exit code whenever verification fails or
    deletion fails, so that a scripted sweep surfaces the problem.
30. As a maintainer, I want the Flux-managed predicate defined in exactly one place, so
    that `migrate` and `cleanup` cannot drift apart in how they classify an App CR.
31. As a maintainer, I want the verification logic reachable without the CLI, so that it
    can be tested directly against constructed cluster responses.

## Implementation Decisions

**Command surface.** `cleanup` takes `--name`, `--namespace`, `--context`, `--dry-run`, and
`--assume-yes`/`-y`. No output flag — nothing is serialized. No values-key flag — no
Conversion happens.

**One API client.** The command creates a single management-cluster client and fetches the
App CR directly through it, rather than going through Fetch. Fetch also retrieves the
Catalog CR and treats its absence as fatal, along with every referenced ConfigMap and
Secret and every dependency HelmRelease. None of that is needed to delete two objects, and
a Catalog CR deleted after migration would otherwise block cleanup for no reason.

**Verification is one function returning a list of failure messages.** Empty list means
pass. It collects every failure rather than raising at the first, so the operator sees the
full picture in one run. It lives alongside the existing deletion logic in the migrator
package, which already performs this kind of I/O and already holds the constants involved.
It makes exactly one API call: the HelmRelease read. Everything else is read from the
already-fetched App CR.

Checks applied to every app:

1. A HelmRelease exists at the App CR's own namespace and name. This is the naming
   convention the converter uses for both the HelmRelease and its source resource. Absence
   short-circuits the remaining checks.
2. Its `Ready` condition is true.
3. It is not suspended. The apply step's revert path suspends the HelmRelease before
   deleting it, so a half-completed revert leaves a suspended HelmRelease still reporting
   ready.
4. Its observed generation matches its metadata generation. Otherwise the ready condition
   describes an older revision than the one gitops last pushed.

Checks 3 and 4 skip rather than fail when the fields are absent; a HelmRelease that has
never reconciled will already have failed check 2.

Checks applied only when the App CR is Flux-managed:

5. The HelmRelease carries the Kustomization name and namespace labels. Because the
   converter strips every `fluxcd.io/` label from generated resources, these can only have
   been stamped on by Flux applying the HelmRelease from the gitops repository.
6. The App CR carries the Kustomization reconcile-disabled label. That label is what evicts
   the object from its Kustomization's inventory; its absence means Flux is actively
   reconciling the App CR and will re-apply it after deletion.

**Check 6 is a label read, not an inventory lookup.** Given the eviction semantics the two
questions are equivalent, and the label read needs no additional API call. Verified on a
live management cluster: an App CR carrying the reconcile-disabled label was absent from
its Kustomization's inventory while nineteen sibling App CRs in the same namespace were
present. For the record, inventory entry identifiers take the form
`{namespace}_{name}_{group}_{Kind}`, with an empty group segment for core resources.

**Deletion reuses the existing routine unchanged in shape**, including its ordering (Chart
CR first, App CR second), its re-application of the suspension annotations, its
single-finalizer removal, and its poll-until-absent behavior. Two changes: a missing Chart
CR is now tolerated and skipped rather than fatal, and a missing App CR at fetch time ends
the command successfully with nothing to do. The first of these also lands in `migrate`,
which shares the routine.

**Remote-cluster apps** resolve a separate workload-cluster client from the kubeconfig
Secret named on the App CR, using the same branch `migrate` uses. Failure to build that
client aborts before anything is deleted. This resolution happens before the dry-run exit,
so a dry run also proves the workload cluster is reachable.

**The Flux-managed predicate moves into one shared helper.** It is currently written out
three times — in the migrate command's cleanup branch, in the reconcile-disabling step, and
now in check 5. Consolidating it is a net deletion.

**`migrate`'s closing message leads with the `cleanup` invocation** and retains the existing
`kubectl` commands beneath it as a documented manual fallback, including the note about
running the Chart CR commands against the workload cluster. The fallback doubles as the
escape hatch should a check ever produce a false positive.

**`migrate`'s non-interactive flag has its help text corrected.** It currently promises to
never auto-confirm the destructive deletion of App and Chart CRs. That promise is scoped to
`migrate` itself; on `cleanup` the same flag does skip the deletion prompt.

**What the tool deliberately does not verify.** `cleanup` never reads the gitops repository
and so cannot prove the App CR manifest was removed from it. Nothing readable from the
cluster distinguishes "removed from git" from "still in git but suppressed by the
reconcile-disabled label" — confirmed on a live cluster, where the app's sibling user-values
ConfigMap remained in the Kustomization inventory. Note also that the relevant Kustomization
runs with pruning disabled, which confirms the recreate mechanism is re-application rather
than pruning: removing the App CR from git deletes nothing, and deleting it by hand while it
remains in git gets it re-applied on the next interval. This gap is addressed by a single
line of text in the pre-deletion summary, not by an additional prompt.

## Testing Decisions

A good test here exercises externally observable behavior: which objects the command reads,
which it deletes and in what order, what it prints, and what exit code it returns. It does
not assert on internal call sequences beyond the ordering guarantees that are themselves
part of the contract (Chart CR before App CR). No test touches a real cluster; the
Kubernetes client is always mocked, kubeconfig loading is always patched, and sleeps are
patched out.

**Seams.** One new seam: the verification function, exercised directly with a mocked
custom-objects client whose responses are scripted per test. This mirrors how the existing
deletion routine is already tested — two mocked clients passed in as arguments, assertions
on call arguments, and API exceptions used to script "not found" responses. The command
itself is tested through the existing CLI-runner seam used for `migrate`, with the client
loader patched to return a mock and prompts driven by supplied input. No new seam is
introduced at the CLI layer.

**Verification function.** Cover the passing case; HelmRelease not found; not ready;
suspended; stale generation; generation fields absent; a Flux-managed App CR whose
HelmRelease lacks the Kustomization labels; a Flux-managed App CR missing the
reconcile-disabled label; a non-Flux-managed App CR skipping both gitops checks; and
several failures returned together in a single call.

**Deletion routine.** Cover a missing Chart CR skipping the chart block and still deleting
the App CR, alongside the existing ordering, re-pause, finalizer, polling, and
partial-failure cases.

**Closing message.** Assert it contains the `cleanup` invocation and still contains both
manual fallback blocks. Existing assertions on this message in the migrate tests need
updating.

**Command.** Cover App CR absent exiting zero; verification failure exiting non-zero
without deleting; dry run exiting zero without deleting; prompt declined exiting zero
without deleting; non-interactive mode deleting without prompting; a remote-cluster app
resolving the workload-cluster client; workload-cluster client failure aborting before any
deletion; and the successful path.

Branch coverage must remain at one hundred percent.

## Out of Scope

- Reading, inspecting, or modifying the gitops repository in any form.
- Detecting whether the App CR manifest is still present in gitops.
- Reverting a completed migration. Still out of scope, as previously decided.
- Any change to Conversion, Resolution, preflight, or Fetch behavior.
- Deleting the generated HelmRelease or its source resource — `cleanup` removes the legacy
  App CR and Chart CR only.
- Batch or multi-app invocation. The command targets one App CR; sweeps are the caller's
  job.
- Diagnostics on failure, such as surfacing events or Helm history.

## Further Notes

Verification was performed against a live management cluster using an App CR that exists
specifically to test this migration tooling. It is a remote-cluster app, currently
mid-migration: suspended and reconcile-disabled by a previous `migrate` run, with a ready
HelmRelease that carries no Kustomization labels and does not appear in its Kustomization's
inventory. It is therefore a live negative test for the gitops-adoption check, available
before any gitops change is made, and it also exercises the workload-cluster branch. Its
already-migrated siblings in the same namespace do appear in the inventory as HelmRelease
entries, confirming the positive case.

The label-stamping behavior underpinning check 5 was confirmed twice over on that cluster:
the App CR carries its Kustomization's name label, and that Kustomization in turn carries
the name label of its own parent Kustomization.
