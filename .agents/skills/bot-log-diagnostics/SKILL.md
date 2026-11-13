---
name: bot-log-diagnostics
description: Analyze AstraBot debug logs to find evidence-backed bugs, state or behavior inconsistencies, stalls, instability, repeated failures, and inefficient behavior. Use for log audits and incident diagnosis; use mapping for requests specifically limited to obf/non-obf protocol mapping.
---

# Bot log diagnostics

Diagnose bot behavior from recorded evidence. Report findings and likely causes; do not modify code, mappings, or a live bot unless the user also asks for a fix or validation.

## Select the evidence

Bot sessions are stored in `resources/debug/bots/<login>/*.debug.jsonl`; completed sessions are gzip-compressed as `*.debug.jsonl.gz`. The schema is defined by the `Debug*Entry` types in `src/services/debug_recorder/recorder.py`.

- Honor a login, file, session, or time range named by the user.
- Otherwise inspect the newest non-empty session across all logins. An active `*.debug.jsonl` may still be empty just after startup; in that case use the newest non-empty compressed session and say so.
- For intermittent or performance symptoms, compare several recent sessions when available. Do not silently generalize from one occurrence.
- Parse JSONL structurally, including gzip input. Preserve timestamps, category, behavior tree, message direction, and message source. Avoid dumping entire message bodies or snapshots when a few relevant fields suffice.

Entries are chronological and use `category` values:

- `log`: `level`, `message`
- `message`: clear/obfuscated type and content, `origin`, `source`
- `behavior`: lifecycle event, state transition, error code, reason, parent, tree
- `state`: behavior boundary snapshot and trigger
- `stuck`: duration without progress, behavior tree, listeners, snapshot, last message

## Find candidates

Build a compact session overview first: duration, category and log-level counts, behavior starts/finishes/errors, stuck events, reconnects or disconnects, and the most repeated warnings/errors/messages. Then investigate suspicious windows rather than reading the whole file linearly.

Prioritize these signals:

- exceptions, tracebacks, `ERROR`/`CRITICAL`, failed assertions, decode failures, and non-success behavior error codes;
- `stuck` entries, long start-to-finish gaps, starts without a matching finish, repeated state transitions, or a parent waiting after a child ended;
- reconnect, retry, timeout, occupied, unavailable, or recovery cycles that repeat without durable progress;
- identical actions or injected client messages repeated at an implausible rate, especially when state snapshots do not change;
- contradictions between a message and the next state snapshot, or between behavior transitions and the recorded behavior tree;
- recurring warnings across sessions, resource growth, queue/backlog symptoms, and work repeated after an already successful outcome;
- latency outliers between causally related events, compared with normal instances of the same sequence in the same logs.

Treat frequency and latency thresholds as triage aids, not proof. Heartbeats, polling, normal retries, server duplicates, and intentionally long waits may be legitimate. Compare the suspicious sequence with successful sequences and inspect the owning code or configuration before calling it a defect.

## Establish causality

For each candidate, reconstruct a bounded timeline around the first failure and at least one repeat:

1. Identify the last confirmed progress or consistent state.
2. Follow the triggering server/client message, behavior transition, logs, and resulting snapshot.
3. Separate the root signal from downstream noise; repeated errors caused by one earlier failure count as one incident pattern.
4. Search the exact log text, behavior, message, state field, and error code in `src/` and relevant tests. Trace through `EventManager` -> frames -> immutable `GameState` -> behaviors when applicable.
5. State confidence explicitly. A bug requires a violated code contract or clear contradiction; otherwise label it a likely issue or an optimization opportunity and name the missing evidence.

If the evidence points to unknown messages, empty decoded content, `unknown_<n>` fields, or a wrong obf/non-obf pair, use the repository's `mapping` skill for that branch instead of improvising a mapping fix.

## Report

Lead with a severity-ordered findings list. For every finding include:

- symptom and user impact;
- exact evidence: session/login, timestamps, categories, and concise field values or counts;
- likely root cause with relevant source file and symbol;
- confidence and plausible alternative explanation;
- recommended fix or next verification step.

Separate confirmed defects, likely issues, and optimization opportunities. Mention explicitly when no actionable anomaly was found, along with the sessions and signal classes checked. Do not present raw occurrence counts alone as bugs.
