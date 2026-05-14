# 3. install/upgrade/rollback/uninstall blocks are omitted

Date: 2026-05-14

## Status

Accepted

## Context

App CRs may carry `spec.install`, `spec.upgrade`, `spec.rollback`, and
`spec.uninstall` blocks that control lifecycle behaviour (timeouts, skipCRDs,
remediation overrides, etc.). HelmRelease has equivalent fields under
`spec.install`, `spec.upgrade`, `spec.rollback`, and `spec.uninstall`.

A scan of 674 real App CRs from production MCs found 51 occurrences of these
blocks. Every occurrence was an empty map (`{}`), with no actual values set —
no timeouts, no skipCRDs, no remediation overrides.

## Decision

The converter emits nothing for these fields. Empty blocks from the App CR are
dropped silently. Non-empty blocks are not a supported input and the converter
makes no attempt to map them.

This keeps the output minimal and avoids encoding lifecycle policy that the
source data does not actually express. The HelmRelease will use Flux's own
defaults, which is the correct behaviour when no explicit policy was set.

## Consequences

- No code changes required — the converter already produces no output for these
  fields.
- If a future App CR with a non-empty lifecycle block is encountered, the
  converter will silently drop it. A validation step or warning would be needed
  to handle that case.
