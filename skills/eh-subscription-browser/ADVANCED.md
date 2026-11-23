# Event Horizon Browser Skill — Advanced & Edge-Case Reference

Read this file on demand when using optional advanced capabilities (`eh_act_batch`, `eh_act_arm`, `eh_collect_sweep`, `META`, `eh_experience`, `collection_policies`), short-term memory, pagination, `receipt_reuse`, `429 subscription_quota`, or error recovery.

---

## 1. Strict `receipt_reuse` Continuation & Wait Windows

- **Single-step `receipt_reuse` permission**: You may execute **one** stable physical action on the previous visible contract without a fresh observation **only** when ALL of the following hold:
  - `observation.status == "receipt_reuse"` AND `observation_delivery.status == "receipt_reuse"`
  - `observation.fresh == false` AND `needs_perceive == false`
  - `receipt.engine_backed == true` AND `receipt.continue == true`
  - No `uncertain` or error flag, and a valid outer `frame_id` is returned.
  - This permission expires after a single use, is **not** a new observation, and cannot serve as new terminal evidence. Ready fields, standalone `continue`, or zero visual change never substitute for this full set of conditions.
- **Segmented wait windows (`wait_seconds`)**:
  - Omitting `wait_seconds` defaults to up to 45s local wait for `eh_act(kind="scroll")` and up to 30s for other commands and `eh_request`.
  - Explicit `wait_seconds` (integer `0..60`) always takes precedence (`0` sends one request without polling).
  - Polling only performs `GET` on the original `request_id`; it never replays `POST` or cancels an already-dispatched action.
  - Returning `pending` / `queued` / `running` with `transport.wait_expired == true` is a normal continuation state, not failure or completion. Continue polling via `eh_request(session_id, request_id)` without fixed sleeps.

## 2. Preflight, Quota & Policy Details

- **Tool drift check**: Compare `eh_status.client.tools` and `eh_status.client.act_kinds` against the host's exposed schemas. If tools like `open_tab`, `close_tab`, `eh_tabs`, or `eh_act_batch` are missing due to stale host cache, ask the user to reconnect the MCP server and re-verify; never call unexposed tools.
- **Metering & quota (`subscription_quota`)**:
  - Read `metering`, `unit`, `limit`, `used`, `reserved`, `remaining`, `enabled`, `expires_at` when available. When `available: false` or omitted, quota is unknown — never guess a fixed frame count or assume zero.
  - `commands/admitted_command` meters billable admitted command units (audit totals may include 0-unit terminals); `frames/successful_frame` meters successful new observation frames. `reserved` is in-flight hold, not remaining balance. Read-only views (`eh_evidence`, `eh_tabs`, `eh_result`) do not consume frames. Task budget `max_steps` is independent of subscription balance.
- **Legacy `entry_policy` (`schema:1`)**: If an older server reports `schema:1` with `match: "exact"`, select an exact URL from `allowed_start_urls`. `available: false` / unknown does not imply unrestricted entry.
- **First `eh_perceive.arguments` limits**: `max_steps`, `pages`, `details_per_page` max `200`; `query` max `8000` chars; canonical UTF-8 JSON of all core arguments max `32 KiB`; task objective max `4000` chars. Initialization-only arguments (`max_steps`, `pages`, `details_per_page`, `contract_mode`, `meta_budget`, `collection_policies`) are rejected on subsequent `eh_perceive` calls.

## 3. Forms, Autocomplete, Dates & Overlays

- **Autocomplete vs. plain search**: Plain text search boxes can be submitted via an explicit search button once verified. City/person/address pickers require choosing a concrete candidate from the new observation and verifying the bound field value and overlay state. Signals such as `needs_selection`, `unknown`, `value_echoed`, or `submitted_unverified` mean verification is still required.
- **Native `<select>`**: Only use `select_option` on genuine single-select native `<select>` controls with exact visible option text. Duplicate/disabled/hidden options are rejected; never pass unexposed HTML `value` attributes. Do not apply `select_option` to custom dropdowns or spam arrow keys + Enter.
- **Focus & dates**: Selecting one field may auto-focus the next field and open a popup — read the new observation before acting. For readonly date triggers, click to open the calendar and select by visible year/month + row/column membership; verify multi-date fields individually before submitting.
- **Overlays**: Resolve or close blocking overlays before interacting with underlying controls. Use `dismiss` only when needed, or `key("Escape")` when focus evidence supports it. Once an overlay closes, all its `O*` IDs become invalid.

## 4. Cross-Frame Evidence (`E*`), Memory & Read-Only Views

- **Freezing via `decision.evidence_patch`**:
  - Format: `"evidence_patch": {"capture": [{"id": "C1", "name": "optional_alias"}]}` (objects only; optional `collection` only when registered at initial perceive).
  - Source IDs must exist in the current visible contract (may include `O0.3`); never pass free-form text, paths, string arrays, stale IDs, `E*`, or `T*`.
  - Max 12 distinct source IDs per call, max 96 frozen `E*` per session. Clipped, oversized, hidden, or sensitive nodes are rejected.
  - Freezing commits only when its carrying action succeeds — always inspect `audited_evidence` (and check for `decision_note` / `audited_evidence_error`). `expand` can both fetch the full contract and commit valid `evidence_patch` entries.
  - `extract.fields` is only an agent note and never upgrades to verified `E*` evidence. No cross-session `E*` import exists.
  - Closing a session does not imply instant destruction of server session evidence; never describe the boundary as "burn-after-read".
- **Short-term memory & ledger**:
  - `decision.ledger`: max 700 chars progress note, committed only on success; not page evidence.
  - `decision.memory_patch`: up to 6 cards, TTL 3 steps, `phase` max 40 chars, up to 4 `ops` (`pin`/`keep`/`update`/`drop`). `pin` requires a current real root card (`card`) also listed in `decision.evidence`, `purpose` in `function|information|candidate`, `summary` max 180 chars, optional `alias` max 16 chars.
- **Read-only recovery**:
  - `eh_evidence(session_id, ids=[...])` (up to 12 IDs) or paginate via `after`/`next_after` (default 12, max 24; `ids` and `after` are mutually exclusive).
  - `eh_tabs(session_id, after?, limit?)` (default 20, max 50, includes closed tabs). `snapshot_only: true` and `can_close` reflect the last snapshot only, not live CDP state.
  - `eh_result(session_id)` recovers a committed terminal receipt even after close (scoped to current account).

## 5. Restricted Batching (`eh_act_batch`)

- Available only when exposed in host schema and the current contract already shows a stable sequence of 1–6 `fill`/`click`/`key` sub-actions with one outer `decision`.
- Sub-actions carry only `kind`/`target`/`query`/`submit` — no sub-decisions, `fields`, `evidence_patch`, or `memory_patch` (neither inside sub-actions nor on the batch wrapper).
- Execution stops early on navigation, candidate popups, focus changes, or any non-strict continuation boundary. Inspect `planned`/`attempted`/`succeeded`/`status`/`stop_reason` and the single final observation (`partial` or `ok: false` does not mean zero actions ran).
- On unknown outcome, poll only the original batch `request_id` with `eh_request`; never blindly resend the batch or tail.

## 6. Optional Advanced Capabilities (Disabled by Default)

Check `eh_status.capabilities` or `eh_capabilities` first (`available: true` required before assuming a group is enabled; tool visibility does not mean server enablement; never probe disabled features with rejected calls):

- **`contract_mode` & `meta_budget`**: Specified once on initial `eh_perceive.arguments` (`contract_mode: "v2"|"grid"`, `meta_budget: {action_attempts: 1..60, perceive_attempts: 1..200}`). Sweep requires `grid`.
- **META patches (`plan_patch`, `strategy_patch`)**: Attached to `eh_act.decision` without extra page actions. `plan_patch` `start` requires `phase/goal/stop_when/action_budget/perceive_budget/next_actions` (budgets max 60, `next_actions` max 8); `strategy_patch` must cite a real current-frame `trigger_event` (`SE*`) and include `visible` in `evidence_scope`. Close any active phase in `eh_terminal.plan_patch` (`complete`).
- **Driving arm (`eh_act_arm`)**: Takes outer `frame_id` and `plan: {id, coverage_mode, checkpoint, actions}` (max 6 actions, each with its own `decision`; omit `base_frame`). `operate`: 2–6 `fill`/`click`/`key` (`key` last only); `collect`: 1 non-top `scroll`; `navigate`: pure `scroll` (skipped regions are never claimed as collected). Requires fresh frame and `operational_confidence.blocking == false`. When `execution_outcome == "arm_handoff"` with outer `status == "completed"`, `uncertain == false`, and a valid new observation, discard remaining planned actions and decide a fresh single step from the new `frame_id`.
- **Collections & sweep (`collection_policies`, `eh_collect_sweep`)**: Register `collection_policies: [{id, prefix, limit, width?, scope: "session", capture_scope: "whole_card"}]` (max 8 groups, total limit max 96) on initial perceive. Before `eh_collect_sweep`, verify fresh `grid`, `operational_confidence.blocking == false`, `sweep_readiness`, and `remaining_checks`. Read every delivered segment in `core_result.frame_deliveries` and the final `observation`; billable frame count never proves content completeness. Keep all `notices` (e.g. `off_domain`, `stalled`) in the final report; only true added/changed/removed clears `stalled`. Additional authorized exact hostnames where configured (e.g. `www.12306.cn`, `kyfw.12306.cn`) do not include subdomains.
- **Experience retrieval (`eh_experience`)**: Call only when `capabilities` reports that group `available: true` (stop on `experience_retrieval_not_open`). Modes: `route(contract, objective)` or `retrieve(contract, task_pattern)` with optional `page_state`, `limit` (`1..3`), `trigger_signature`. Select up to 3 returned refs for `experience_refs`; submit `experience_feedback` / `experience_iteration` only on terminal top-level fields. Experience is generic procedure, never current-page evidence.

## 7. Recovery, `429` Quota, Retention & Terminal Edge Cases

- **In-page updates (`navigated: false`)**: Candidate selection, sorting, and filtering often update in-place; judge by field values, order, selection state, or overlay changes.
- **Tab lifecycle**: Reuse an owned relevant tab via `switch_tab` when appropriate; `open_tab` must obey `entry_policy`. Reserve 2–3 commands for `eh_tabs` + `close_tab` before `eh_terminal` (`eh_terminal` ends the session, and `eh_close_session` does not close browser tabs or exit Chrome).
- **429 `subscription_quota`**: Inspect actual balances via read-only `eh_status`; never retry billable requests under new IDs. When remaining balance is confirmed 0, stop billable actions; if `terminal_units: 0` is active and valid evidence already exists, commit `eh_terminal` and close (otherwise deliver `partial` or `failed`).
- **Content retention & legacy replay**: Current servers deliver content ephemerally in bounded RAM and clean up after close. `content_not_retained` / `content_available: false` means body is unavailable, not that the action never ran — query the original ID read-only and report honestly. If a legacy adapter returns `local_replay: true` or `historical: true`, reject it as fresh observation/action permission and re-perceive.
- **Terminal binding limits**: Max 32 `bindings` on `eh_terminal`; if distinct bound IDs exceed 12, explicitly choose up to 12 for `decision.evidence`. Verify `commit_state == "committed"` and `terminal_commit.state == "committed"`; on `unconfirmed` / `no_verified_terminal_receipt`, recover via `eh_result(session_id)` instead of replaying.
