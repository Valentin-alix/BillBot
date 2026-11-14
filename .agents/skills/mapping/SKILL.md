---
name: mapping
description: Scan latest bot debug log for obf<->non-obf mapping incoherences, propose fixes (pipeline logic fix first, then proto field addition, then pinned_pairs fallback), optionally verify live via sandbox
---

# Mapping incoherence triage

Find obf/non-obf protocol mapping bugs from a bot's debug log, propose a fix, verify live if the
bug blocks the bot.

## 1. Locate the log

Debug logs live at `resources/debug/bots/<login>/*.debug.jsonl` (repo root, see
`src/consts.py:BOT_DEBUG_LOGS_DIR`; older sessions are gzip'd: `*.debug.jsonl.gz`). One JSONL
entry per line, `category` in `log|message|behavior|state|stuck`. Message entries carry both
sides of the mapping:

```json
{"category": "message", "unobfuscated_type": "...", "obfuscated_type": "...",
 "obfuscated_content": {...}, "unobfuscated_content": {...}}
```

Pick the most recent `*.debug.jsonl` across all logins (by mtime) unless the user names an
account.

## 2. Spot incoherences

Grep/parse the JSONL for:

- `unobfuscated_type` containing `Unknown` → message never mapped.
- `unobfuscated_content` is `null`/empty while `obfuscated_content` has data → decode/field mapping
  failure.
- Non-obf field keys named `unknown_<n>` holding non-default values → unmapped field.
- A `stuck` entry whose `last_message`/`snapshot` references one of the above → the gap is
  actually blocking a behavior (bump priority).
- A `behavior` entry with an error right after a suspect `message` entry — correlate by timestamp.

Cross-reference each candidate against `DBDofusUnity/datas/proto_mapper/pinned_pairs.json`
(already pinned but maybe wrong field) and `DBDofusUnity/datas/protos/game_mappings.json` (current
resolved mapping) before concluding something is actually broken.

## 3. Propose a fix, most durable first

Try each in order; only drop to the next when the current one doesn't hold up. State which one
you're proposing and why the more durable options don't apply.

1. **Pipeline logic error.** The matcher itself picked the wrong candidate — a wrong, missing, or
   mis-weighted signal in `proto_mapper_assembly/matching/` (`score_constraints.py`,
   `score_lookup.py`, `score_preparation.py`, `static_scores.py`, `runtime_rescore.py`),
   `affinities/`, or `scoring/message_scoring.py`. Diagnose with:
   - `uv run python -m scripts.mapping.debug.inspect_message_pair --obf <O> --non-obf <N>` — breaks down the score for one
     specific pair, showing which signal drove it.
   - `uv run python -m scripts.mapping.debug.rank_group_candidates --incoherent` — lists non-obf `.proto` files whose messages are
     split across several obf groups (a scattered file means the mapping is wrong, not that the
     file moved; nothing written, read-only).
   - `uv run python -m scripts.mapping.debug.rank_group_candidates --non-obf <X> --members` — for one file, ranks the obf groups it
     could belong to and shows the message-level assignment inside the best candidate, so you can
     see which claim is a real pin vs. an algorithm guess.
   Fix the metric/heuristic (add a missing signal, correct a wrong one, adjust a weight/threshold
   like `_STORE_MATCH_BONUS`/`_PARENT_CANDIDATE_THRESHOLD`), then `run-pipeline` and
   `benchmark_cross_build.py --mode current` (or `--mode cross-build`) to confirm no regression on
   other pairs. Use this when the mistake would recur on the next build, not just here.

2. **Field proved missing and not dead.** The obf side carries a field with no non-obf
   counterpart, the parent message's own mapping is otherwise solid, and the field is confirmed
   live — not just a decode gap — via
   `uv run python -m scripts.mapping.check_zero_access_fields --unknown-fields` (classified `active`, not
   `dead_candidate`/`inconclusive`: a non-default runtime value in the log plus an access recorded
   in `datas/proto_mapper/{obf,non_obf}/proto_accesses.json`, IDA trace). If proved, add the field to the non-obf `.proto`
   definition (pins can't add fields that don't exist non-obf). If the parent message's mapping is
   itself uncertain, or the field can't be named confidently, drop to 3.

3. **Pinned fallback.** No durable rule would survive the next build (name collision, ambiguous
   shape, uncertain message mapping, one-off). Add to `pinned_pairs.json`.

For any change to `.proto`, apply in this order:
`uv run python -m scripts.maintenance.main synchronize-protos` → edit `pinned_pairs.json` (by hand, or live via
sandbox, step 4) → `uv run python -m scripts.maintenance.main synchronize-protos` again, so the export stores the
field-mapping signatures the new pin produced.

For any change to `pinned_pairs.json` then run uv run python -m scripts.maintenance.main run-pipeline

## 4. Live-verify if the mapping blocks the bot

If the incoherence is blocking (a `stuck` entry, a behavior stall), verify against a running bot
before calling it done:

1. Ensure `SANDBOX_ENABLED=1` and the bot is running (headless or GUI), or start it that way.
2. `python scripts/sandbox_client.py --list` to find the login, then push the fix:
   ```
   python scripts/sandbox_client.py --login <login> --code "add_pinned_pair('<ObfMsg>', '<NonObfMsg>', {'<obf_field>': '<non_obf_field>', ...})"
   ```
   Writes to `pinned_pairs.json` AND hot-patches the in-process `game_mappings` cache — no restart
   needed.
3. Re-trigger the message (`replay_behavior`, `trigger_behavior`, or wait for it live) and inspect
   `game_state`/behavior output in the same sandbox session to confirm the fix resolves the
   incoherence before reporting done.

## Output

Report: which message/field, what was wrong, the fix applied, and — if live-verified — the
sandbox evidence confirming it.
