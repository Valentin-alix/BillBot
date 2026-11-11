# BillBot

Python 3.15.2, Windows only. Bot: `src/`; launcher: `AnkamaLauncherEmulator/`; protocol tools: `DBDofusUnity/`.

## Validation

Run commands from the repository root:

- Targeted tests: `uv run pytest tests/path/to/test_file.py`.
- Lint without edits: `uv run ruff check .`.
- Types: `uv run pyright`.
- Final gate for code changes: `uv run poe verify` (also applies Ruff fixes).

During iteration, run only the relevant check or test. For documentation-only changes, verify paths, commands, and `git diff --check`; application tests are unnecessary.

## Code and tests

- Comment only non-obvious constraints or reasoning.
- Validate and type external input at boundaries; reuse existing schema models. Avoid speculative fallbacks and `# pyright: ignore`.
- Validate behavior changes before adding or updating tests. Test observable behavior and regressions, not implementation details or tuning values.

## Exploration

- Avoid broad reads/searches of `DBDofusUnity/datas/**`, `DBDofusUnity/Il2CppInspectorRedux/**`, `DBDofusUnity/protodec/**`, `DBDofusUnity/UABEA/**`, and `resources/human_sessions.json` unless needed.
- For protocol definition or mapping changes, read `DBDofusUnity/proto_mapper_assembly/AGENTS.md`, including when editing definitions outside that subtree.
