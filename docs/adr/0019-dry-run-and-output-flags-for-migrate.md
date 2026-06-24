# Dry-run and manifest output flags for the migrate command

Two orthogonal flags are added to `migrate`: `--dry-run` and `--output FILENAME`.

`--dry-run` stops the command after displaying the generated Flux resources — before the confirm prompt and before any cluster mutation. It prints `✅ Dry run complete — halting before live migration.` and exits 0. The flag does not shorten the read-only phase: Fetch, Preflight, Resolve, and Conversion all run in full, because the Resolver requires live cluster data to produce a correct `valuesKey` mapping.

`--output FILENAME` writes the generated manifests as plain multi-doc YAML to the given file. It is independent of `--dry-run`: the file is written whether or not the live migration proceeds. After writing, a message is printed to stdout: `✅ Conversion result saved to FILENAME!`, with a blank line before it and before the next section header, so it reads as visually distinct from both the conversion output and the migration progress.

Stdout-piping (`--output -`) was considered and deferred — the value is not yet justified and the channel-separation it requires (moving Rich UI to stderr) is non-trivial.
