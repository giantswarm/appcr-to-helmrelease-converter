# 7. Catalog CR is always a required second input; conversion path from repository type

Date: 2026-05-20

## Status

Proposed

## Context

The original converter accepted a single input (the App CR) and hardcoded the GS
catalog OCI URL as `oci://gsoci.azurecr.io/charts/giantswarm/{chart}`. To support
non-GS catalogs (Path B — HelmRepository + HelmRelease), the converter needs the
Catalog CR's HTTP URL, which cannot be derived from the App CR alone.

Three options were considered for how the converter obtains catalog information:

1. **Static table in code** — embed the known GS catalog `(name, namespace)` pairs.
   Derive the OCI URL from a hardcoded pattern for GS catalogs; require a URL flag
   for non-GS catalogs.
2. **Optional second input** — Catalog CR provided only for non-GS catalogs; GS
   catalog path continues to derive the URL from a static table.
3. **Always-required second input** — both App CR and Catalog CR are always
   provided. The converter reads `spec.repositories[].type` from the Catalog CR to
   decide the path and reads the URL directly from the Catalog CR.

Option 3 was chosen. Since the Catalog CR is always available, the GS vs non-GS
distinction becomes human context only — the converter needs no knowledge of it.

## Decision

The converter always receives two dicts: `(app_dict, catalog_dict)`. The conversion
path is determined purely by the Catalog CR:

- `catalog_dict["spec"]["repositories"]` contains an entry with `type: oci` →
  **Path A**: produce an OCIRepository + HelmRelease. The OCIRepository `spec.url`
  is `{repositories[type=oci].URL.rstrip("/")}/{app.spec.name}`. `oci` type is
  preferred if multiple types are present.
- No `oci` entry; `type: helm` present → **Path B**: produce a HelmRepository +
  HelmRelease. The HelmRepository `spec.url` is the first `type: helm` entry's URL.
  A preflight warning is emitted when multiple `helm` entries are present.

The static GS catalog lookup table is removed from code entirely. It is retained in
`CONTEXT.md` as human reference only.

Both input sources supply the Catalog CR as a second YAML document:

- **Offline `convert`**: stdin (or a file argument) carries a multi-doc YAML with
  App CR and Catalog CR separated by `---`. Parsed with `yaml.safe_load_all()`;
  documents identified by `kind`.
- **`convert-from-cluster`**: the `fetcher/` package fetches both CRs from the MC
  (see ADR 0009).

### HelmRelease for Path B

Path B produces a `spec.chart` block instead of `spec.chartRef`:

```yaml
spec:
  chart:
    spec:
      chart: <app.spec.name>
      version: <app.spec.version>
      sourceRef:
        kind: HelmRepository
        name: <helmrelease-name>
        namespace: <helmrelease-namespace>
```

The empty-version hard error from ADR 0002 applies to both paths.

## Consequences

- The converter is simpler: no static table, no hardcoded URL strings. The Catalog
  CR is the single source of truth for the chart registry URL and type.
- All input sources must provide a Catalog CR. Users converting apps offline must
  include the Catalog CR in their multi-doc YAML.
- Adding a new catalog type in the future requires no code change — the converter
  reads whatever type the Catalog CR declares.
- The static GS catalog name/namespace table in `CONTEXT.md` remains useful for
  human reference but is no longer a code dependency.
