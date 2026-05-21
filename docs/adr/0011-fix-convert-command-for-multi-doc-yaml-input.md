# 11. Fix convert command to accept multi-doc YAML and read catalog URL from Catalog CR

Date: 2026-05-21

## Status

Accepted

## Context

The `fetch` command (ADR 0010) was designed to emit a two-document YAML stream
(Catalog CR first, App CR second, separated by `---`) that can be piped directly
into `convert`. In practice this pipeline crashed immediately:

```
python main.py fetch --name my-app --namespace giantswarm | python main.py convert
yaml.composer.ComposerError: expected a single document in the stream
```

Two bugs were present:

**Bug 1 — single-document parser.** `convert_cmd` called `yaml.safe_load(content)`,
which raises `ComposerError` on any input with more than one YAML document. The
multi-doc input design from ADR 0007 was implemented in `fetch` but not yet
reflected in `convert`.

**Bug 2 — hardcoded OCI URL.** `build_oci_repository` constructed the OCIRepository
`spec.url` from a hardcoded base (`oci://gsoci.azurecr.io/charts/giantswarm/`).
This produced silently wrong output for any App CR whose Catalog CR pointed to a
different registry. ADR 0007 specified that the URL must be read from
`catalog_dict["spec"]["repositories"]`, but the implementation had never been
updated to do so.

## Decision

Both bugs were fixed together because they share a root cause (the converter's
interface predated the Catalog CR design) and fixing one without the other would
leave the pipeline in a broken-but-silent state.

**Parse multi-doc YAML in `convert_cmd`.** `yaml.safe_load()` was replaced with
`yaml.safe_load_all()`. A new `_identify_docs()` helper identifies the App CR and
Catalog CR by `kind` + `apiVersion: application.giantswarm.io/v1alpha1`. Docs with
an unrecognised `apiVersion` or `kind`, and empty/null documents produced by bare
`---` separators, are silently skipped. Exactly one App doc and exactly one Catalog
doc are required; missing or duplicate docs produce a hard error with a clear
message and exit code 1. Document order is not significant.

**Change the converter's public interface.** `converter.convert(content: str)` was
changed to `converter.convert(app_dict, catalog_dict)`. YAML parsing is the CLI
layer's responsibility; the `converter/` package receives pre-parsed dicts. This
matches ADR 0007's intent and keeps the `converter/` package free of I/O.

**Read the OCI URL from the Catalog CR.** `build_oci_repository(app)` was changed
to `build_oci_repository(app, catalog)`. The URL base is extracted via
`_oci_url_from_catalog()`, which checks `spec.repositories` for an `oci`-typed entry
first, then falls back to the deprecated `spec.storage` field (matching the lookup
order specified in ADR 0007). A hard error is raised if neither field yields an
`oci` entry.

## Consequences

- `fetch | convert` works as designed: the two-document stream emitted by `fetch`
  is accepted by `convert` without error.
- The converter no longer contains any hardcoded catalog URLs. The Catalog CR is
  the sole source of truth for the chart registry URL and type.
- `converter.convert()` is a breaking interface change. All callers (CLI and tests)
  must pass two dicts. Single-document YAML piped into `convert` is now a hard
  error instead of silent success.
- The `fetch-and-convert` command (planned, ADR 0008) can call `converter.convert()`
  directly with the dicts returned by `fetcher.fetch()` — no intermediate YAML
  serialisation or parsing required.
