# 1. Pure functions in the converter package; I/O owned by the CLI

Date: 2026-05-13

## Status

Accepted

## Context

The original `main.py` mixed conversion logic, YAML serialization, and stdin/stdout
I/O in a single file. When splitting the converter into a package and introducing a
click-based CLI, we had to decide where serialization and I/O belong.

Alternative considered: have `convert()` accept a file-like object and write YAML
directly to an output stream, keeping the call site in `main.py` minimal.

## Decision

The `converter` package exposes only pure functions: dict in, dict out, no I/O, no
YAML serialization. All side effects (reading stdin, dumping YAML to stdout) live
exclusively in `main.py`, which owns the click CLI group and all subcommands.

## Consequences

- Converter functions are trivially testable: pass a dict in, assert on the dict out.
  No stream mocking, no stdout capture.
- A reader encountering `build_helm_release` must look to `main.py` to understand
  how the result reaches stdout — this file documents that choice.
