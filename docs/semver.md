# Semantic Versioning Policy

avo follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
The public API surface stabilizes during **0.7.x** towards a frozen contract
at version **1.0.0**; any change to a documented surface requires a
corresponding SemVer bump.

## Version grammar

Given a version `MAJOR.MINOR.PATCH`:

- `MAJOR` — incompatible API changes.
- `MINOR` — backwards-compatible new functionality.
- `PATCH` — backwards-compatible bug fixes.

Pre-1.0 (`0.x.y`) signals an unstable API; minor bumps may include
breaking changes until `1.0.0` is released.

## What counts as public

A symbol, file, environment variable, or CLI form is **public** when
it appears in one of the following locations:

- `docs/api-stability.md` (the canonical list).
- Any module's `__all__` attribute.
- Any export from the `avo` package root.
- Any `AVO_*` environment variable consumed by the runtime.
- Any `avo <command>` form documented in the README or `avo --help`.
- Any public `ProviderAdapter`, `FunctionTool`, or hook callback
  signature in `avo.providers`, `avo.tools`, or `avo.hooks`.

Everything else is **internal** and may change without notice,
including (but not limited to):

- The on-disk layout of the SQLite event log.
- The wire format of any internal protocol.
- The exact contents of `tracing.TraceInspector` text output.
- The `avo_core` Rust wheel's PyO3 surface (when applicable).

## Deprecation process

Breaking changes follow a three-step process:

1. **Deprecate.** Mark the symbol with a `DeprecationWarning` and
   add a `@deprecated` decorator (or equivalent marker) one minor
   release ahead of removal.
2. **Document.** Add an entry under the `Deprecated` section of
   `CHANGELOG.md` for the release that introduced the deprecation.
3. **Remove.** Removal happens on the next `MAJOR` bump (or on a
   subsequent `MINOR` bump while the project is still pre-1.0, with
   the same `Deprecated` entry updated to `Removed`).

The full process applies to:

- Removed or renamed public exports.
- Removed or renamed `AVO_*` environment variables.
- Removed or changed CLI subcommands or flags.
- Changed `ProviderAdapter` or `FunctionTool` method signatures.

## Patch-level changes

Bug fixes that do not alter observable behavior may ship as a
`PATCH` bump without prior notice. Examples:

- Internal refactors with no API change.
- Performance improvements that preserve output.
- Documentation corrections.
- Test-only changes.

## Pre-1.0 caveat

While the major version is `0`, the `MINOR` position is treated as
the breaking-change boundary. A bump from `0.1.x` to `0.2.0` may
include removals of symbols deprecated in `0.1.x`. The deprecation
process above still applies — symbols land in `Deprecated` first
and stay for at least one minor release before removal.

## How to propose a breaking change

1. Open an issue describing the change and migration path.
2. Wait for maintainer sign-off before implementing.
3. Add the symbol to `Deprecated` in `CHANGELOG.md` for the
   release that ships the deprecation.
4. Ship the removal on the next breaking-change release.

## Stability guarantees

| Surface | Pre-1.0 | Post-1.0 |
| --- | --- | --- |
| Public exports listed in `docs/api-stability.md` | may change with deprecation notice | frozen |
| `AVO_*` environment variable names | may change with deprecation notice | frozen |
| CLI subcommand grammar | may change with deprecation notice | frozen |
| SQLite event-log schema | may migrate; migrations shipped | frozen; migrations only |
| `ProviderAdapter` protocol | may add new optional methods | frozen |
| Hook event names | may add new event names | frozen |

## Versioning of optional dependencies

Dependencies under `[project.optional-dependencies]` (e.g. `[otel]`,
`[sandbox]`, `[mcp]`) follow their own SemVer cadence. Avo bumps
the minimum required version of an optional dep only when:

1. The new version is required to fix a known incompatibility, or
2. The old version reaches end-of-life upstream.

Optional dep changes never trigger an avo MAJOR bump.
