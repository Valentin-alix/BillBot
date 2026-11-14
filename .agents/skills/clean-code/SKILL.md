---
name: clean-code
description: Diagnose dead code, overkill/useless code, useless tests, over-verbose docstrings/comments, misplaced code, and bad practices in a target scope (diff, file, dir), then propose or apply fixes
---

# Clean code audit

Diagnose-first pass over a scope (default: current diff; else a file/dir/PR the user names).
Report findings before editing anything, unless the user already said to apply fixes.

## 1. Scope

Default to `git diff` (staged + unstaged) against `master`. If nothing changed, ask what to scan
instead of guessing broad. Never sweep `DBDofusUnity/datas/**`, `Il2CppInspectorRedux/**`,
`protodec/**` (generated/vendored, see AGENTS.md).

## 2. Dead code

- `uv run poe dead_code` (vulture) on the touched files/modules — treat its hits as leads, not
  verdicts; confirm each with a `Grep` for the symbol before flagging (vulture false-positives on
  dynamic dispatch, PyQt signal handlers, pydantic fields).
- Unreachable branches, functions/classes with zero callers repo-wide, commented-out
  code blocks left in place.

## 3. Overkill / useless code

- Abstractions (base classes, factories, config flags) with exactly one implementation and no
  second caller in sight — per AGENTS.md/AGENTS.md, prefer the concrete version.
- Defensive `try/except`, null-checks, or validation guarding a case that can't occur at that call
  site (internal code, framework-guaranteed invariants) — only boundary code (user input, external
  API responses) should validate.
- Backwards-compat shims: unused `_renamed` vars, re-exports of removed types, `# removed` comments,
  feature flags for dead paths.
- Wrappers/helpers used exactly once that just rename a call — inline them.

## 4. Useless tests

- Tests that assert a mock's return value equals what the test itself configured the mock to
  return (tests the mock, not the code).
- Duplicate tests covering the identical path with cosmetic input changes.
- Tests with no real assertion (`assert True`, assert-free smoke tests that only check "didn't
  raise" where a raise was never plausible).
- Skipped/xfail tests with no linked reason or stale reason.

## 5. Verbose docstrings/comments

- Multi-paragraph docstrings on straightforward functions — condense to one line, or drop if the
  signature + name already say it.
- Comments restating WHAT the code does instead of a non-obvious WHY (hidden constraint, workaround,
  invariant).
- Comments referencing the current task/issue/caller ("used by X", "fix for #123") — these belong in
  commit/PR description, not source.

## 6. Misplaced code / bad practice

- Code living in a module inconsistent with the Architecture Map in AGENTS.md (e.g. protocol
  decoding logic inside a GUI widget, behavior logic inside `EventManager`).
- Business logic in `__init__`/constructors that belongs in a method; GameState mutated in place
  instead of producing a new immutable frame; blocking calls inside async/signal handlers.
- Pyright violations papered over with narrow-then-ignore instead of `model_validate`/explicit
  `Any` annotation (see AGENTS.md Pyright strictness section).
- Duplicated logic that already exists elsewhere in the module/package (near-identical function a
  few files away) — candidate for reuse instead of a new copy.

## Output

For each finding: `path:line — category — one-line problem — proposed fix`. Group by category,
most confident/impactful first. Do not apply fixes unless the user asks; if they do, apply then
re-run `uv run poe lint` / relevant test file to confirm nothing broke.
