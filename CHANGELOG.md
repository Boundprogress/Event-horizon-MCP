# Changelog

All notable updates to the open **Event Horizon thin client** (`Event-horizon-MCP`, `AGPL-3.0-only`) and its paired **hosted perception core** (`api.ehbrowser.com`) are documented here and mirrored at [ehbrowser.com/updates](https://ehbrowser.com/updates/).

---

## 2026-11-25 — 3×–7× Faster Cross-Ocean Perception & Scrolling + Persistent HTTPS Keep-Alive

### Thin Client (`Event-horizon-MCP`)
- **Pure-stdlib HTTP/1.1 Keep-Alive connection pool (`client.py`, commit `96842c6`)**:
  - Added a thread-safe persistent connection pool using Python's standard `http.client`, `ssl`, `select`, and `socket` (`TCP_NODELAY` + `Accept-Encoding: gzip` + `atexit` cleanup).
  - Eliminates repeated TLS handshakes across persistent MCP (`client.py mcp`), Python SDK (`eh_sdk.py`), and multi-request CLI flows (`eh_create_session`: `2.26s → 1.11s`, `eh_close_session`: `2.06s → 0.48s` on a ~512 ms RTT trans-Pacific link).
  - **Fail-closed safety invariants preserved**: direct TLS only (no proxy interception), zero automatic redirect following, non-blocking socket liveness pre-check before reuse, **never retries non-idempotent `POST` requests** (idempotent `GET` retries once on a server-close race).
  - Added unit test suite `tests/test_client_keepalive.py` (6/6 passing); full thin-client suite **64/64 passing** and `client.py audit` passes with `stdlib_only: true` and `0` findings.
- **One-call session lifecycle batching (`example_agent.py`, `eh_sdk.py`, `skills/eh-subscription-browser/SKILL.md`)**:
  - `eh_create_session` now supports optional `auto_perceive=true` (plus optional `perceive_request_id` and `perceive_wait_seconds`), returning `session_id`, `frame_id`, and the initial Viewport Contract in a single round-trip while preserving deterministic, query-only replay safety (`POST` is never re-sent on repeat).
  - `eh_terminal` now supports optional `close_session=true`, automatically closing the session only after a verified `committed` terminal (`ok: true`) and leaving it open for inspection on any failure or pending state.
  - Updated `EventHorizonClient.open_session(..., auto_perceive=True)`, `EHSession.terminal(..., close_session=True)`, and `SKILL.md` guidance; added `tests/test_client_lifecycle_batch.py` (5/5 passing; full public suite **69/69 passing**, `client.py audit` `stdlib_only: true`, `0` findings).

### Hosted Perception Core (`api.ehbrowser.com`, zero client breaking changes)
- **Low-RTT browser tunnel, pipelined click/fill & delta Viewport Contracts**:
  - Removed unnecessary browser debugging setup round-trips, reused active browser connections across same-page actions (`scroll`, `click`, `fill`) and automatic post-action observation, pipelined trusted mouse/wheel events, bounded post-action settle windows on animated pages, and avoided re-uploading unchanged page perception scripts on warm pages.
  - Enabled incremental Viewport Contract delivery on same-document scrolls (emitting only newly visible, changed, or focus cards while automatically falling back to a full contract on navigation, document change, or recovery) to cut repeated prompt tokens across multi-scroll tasks.
  - **Live trans-Pacific Amazon search benchmark** (~512 ms base RTT, persistent `client.py mcp`):
    - Steady-state `eh_perceive`: **`19.2s → 6.62s`** (~2.9× faster)
    - Steady-state `eh_act(scroll)` + automatic observation: **`49.5s → 6.18–8.00s`** (avg **`7.13s`**, ~6.9× faster, `0` pending polls)
    - Preserves identical Viewport Contract output and `isTrusted=true` browser input semantics.

### Ecosystem
- Officially reviewed and listed on [mcpservers.org](https://mcpservers.org/).

---

## 2026-11-24 — One-Click GitHub & Google Sign-In

- **Local Console OAuth 2.0 (`panel.py`, `panel.html`, `panel.js`, commit `beef113`)**:
  - Added **Continue with GitHub** and **Continue with Google** one-click sign-in directly in the local console; verified provider emails skip the 6-digit email verification step and link cleanly to existing verified accounts.
  - Kept 100% pure Python stdlib with zero OAuth client secrets stored on disk; email/password login remains available and all safety, GeoIP, single-concurrency, and Terms confirmation gates remain enforced.
- **Documentation & Transparency (`README.md`, `README.zh-CN.md`, commits `33ea609`, `70e8613`)**:
  - Clarified why the perception core is hosted (abuse prevention, consistent sensitive-site/payment/credential blocking, and free personal access) and added voluntary sponsorship information.
  - Added `glama.json` for Glama MCP directory verification.

---

## 2026-11-23 — Overlay & Transparent Control Fixes, Lean Agent Skill & 3-Layer Integration

- **Hosted Core (general fix, zero site rules)**:
  - Improved recognition and dismissal of card-structured modal overlays and accurately bound transparent native button overlays without relaxing occlusion or disabled-state safety guards.
- **Thin Client & Agent Skill (`skills/eh-subscription-browser/`, `eh_sdk.py`, `client.py`, commit `ea5fd6a`)**:
  - Streamlined `SKILL.md` with a compact notation cheat sheet and split advanced multi-tab/batch workflows into `ADVANCED.md`.
  - Added direct CLI tool invocation (`client.py tool`), ACP config export (`mcp-config --host acp`), Python SDK (`eh_sdk.py`), Windows path handling fixes, and automated schema checks across all 17 MCP tools.
- **Engineering Essay & Terms**:
  - Published [Compiled Viewport Perception: Reading Rendered Signals Instead of Raw DOM Dumps](https://ehbrowser.com/blog/compiled-viewport-perception/) and updated Terms to `v2026-11-23.1`.

---

## 2026-11-21 — Public Free Personal Launch & Open AGPL-3.0 Thin Client

- Launched **100% free personal access** at [ehbrowser.com](https://ehbrowser.com/) and `api.ehbrowser.com` with email verification, no waitlist, and no credit card.
- Open-sourced `Event-horizon-MCP` under `AGPL-3.0-only` with zero pip dependencies and 3-layer agent integration (MCP stdio, CLI tools, and Python SDK).
- Published 5 live 2K viewport benchmarks (Apple SEC 10-K, Wikipedia, Bootstrap 5.3 modal, UN 240-row table, arXiv) showing 91.9% fewer tokens and 0 occluded clicks.
