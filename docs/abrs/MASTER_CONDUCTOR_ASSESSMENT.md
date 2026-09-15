# ABRS Master Intelligence Conductor — Viability Assessment

**Status:** design document. No code is wired into the engine by this branch.
**Audited against:** `georgiananup-gif/ABRS-Market-Research-Engine_Apr2026` @ `99e5f0f`
**Date:** 2026-09-15

---

## TL;DR

- A Master Conductor is **viable and worth building — but not as a new regime engine.** `intel_brief.py` already is one (1,071 lines, 6 adapters, wired into `run.py:243`). Building a second scorer creates two contradicting market calls.
- The real gap is **coverage and narrative**: 21 monitors exist, only **6** reach the synthesis. Breadth, Accumulation, Sector Rotation, Soldiers, Scouts, ETF, Value and Movers — the exact modules the conductor brief names — feed **nothing**.
- Recommended shape: **extend `intel_brief` from 6 → 12 adapters**, then add a thin `master_conductor.py` on top that does reconciliation + narrative only. Roughly 1 week of work, ~600 new lines, no layer-contract breakage.

---

## 0. What I could and could not verify

| Claim | Status |
|---|---|
| Module inventory, function signatures, adapter count, config weights | **Verified** — read from source at `99e5f0f` |
| A live market regime call / today's dashboard | **Cannot produce.** `data_store/price_history/` contains **0 files**, there is no `.env`, no `output/`. The engine cannot execute in this container. |

The conductor brief asked me to read all module outputs and emit a live dashboard. **I did not do that, and I will not simulate it.** Any regime, gate or ticker call I wrote without that data would be fabricated. What follows is the architecture and the templates that produce that dashboard once run against real data.

---

## 1. Current state — audited

### 1.1 You already have two conductors

**Conductor A — `intelligence/intel_brief.py` (live).**
Wired into `run.py:243`. Produces `IntelBrief`: composite regime 0–100, five buckets (`RISK_ON` → `RISK_OFF`), most-restrictive-wins gates for LEAP/CSP/CC/WHEEL, A/B/C ranked candidates, a conflict log, and a `snapshots_available / snapshots_total` reliability counter. Frozen dataclass contracts in `intel_brief_types.py`. Renderer in `reporting/intel_brief_html.py`. **This is a real, disciplined synthesis layer.**

**Conductor B — `master_intelligence.py` v0.1 (orphaned).**
A 52 KB scaffold ("The Conductor") with `MonitorSignal`, `KeyStock`, `mentor_rules.yaml` and a `MASTER_INTELLIGENCE_BLUEPRINT.md`. It exists in this repo **only as a text dump** at `intelligence/master_intelligence.txt`, captured from a different machine. The package directory does not exist. It is littered with `WIRE:` and `[Unverified]` markers — it was never connected.

**The risk in the request as written:** "build a master module that reads all modules and emits one regime" is a description of Conductor A. Executing it literally produces Conductor C, and three modules that disagree about whether LEAPs are open.

### 1.2 The coverage gap — this is the actual problem

`_load_all_snapshots()` calls **6** adapters. The repo has **21** monitors.

| Monitor | `get_*_state()` exists? | Adapter? | In composite? |
|---|---|---|---|
| generals_monitor | ✅ | ✅ | ✅ 0.30 |
| mega_cap_tech | ✅ | ✅ | ✅ 0.20 |
| trendline_monitor | ✅ | ✅ | ✅ 0.20 |
| rally_breadth_monitor | ✅ | ✅ | ✅ 0.15 |
| recovery_pulse | ✅ | ✅ | ✅ 0.10 |
| breakout_scanner | ✅ | ✅ | ❌ candidates only |
| **breadth_monitor** | ✅ `get_breadth_state` | ❌ | ❌ |
| **accumulation_monitor** | ✅ `get_accumulation_state` | ❌ | ❌ |
| **sector_rotation_monitor** | ✅ `get_sector_rotation_state` | ❌ | ❌ |
| **soldiers_monitor** | ✅ `get_soldiers_state` | ❌ | ❌ |
| **scouts_monitor** | ✅ `get_scouts_state` | ❌ | ❌ |
| **movers_scanner** | ✅ `scan_movers` | ❌ | ❌ |
| **etf_monitor** | ❌ report-shaped only | ❌ | ❌ |
| **value_stocks** | ❌ report-shaped only | ❌ | ❌ |

Six monitors are **adapter-ready today** — they already return a single state object and take `router`. Two are not.

**Consequence:** the current composite regime is **97% a mega-cap leadership score.** Generals + MegaCapTech + Trendline = 0.70 of the weight, and all three read the same ~11 large-cap names. `rally_breadth` at 0.15 is the only genuine breadth input. There is no accumulation/distribution input, no sector rotation input, and no soldiers-vs-generals divergence input — the three signals that historically turn *before* mega-cap price does. **The engine is structurally late.**

### 1.3 Two defects found while auditing

1. **Quorum hole.** `_composite_regime()` redistributes weight proportionally across surviving adapters. If 4 of 6 fail, the remaining 2 are renormalised to 1.0 and the brief publishes a confident-looking score with no visible degradation beyond a counter. A 2-of-6 brief and a 6-of-6 brief are indistinguishable in the number. **A conductor must refuse to call the regime below quorum, not average over holes.**
2. **Name collision.** `breadth_monitor.get_breadth_state` and `rally_breadth_monitor.get_breadth_state` are different functions with the same name returning different `BreadthState` types. Adapters must import with explicit aliases or this will silently bind to the wrong module.

---

## 2. Verdict on viability

**Viable — conditionally.** Conditions:

| # | Condition | Why |
|---|---|---|
| 1 | Conductor **consumes** `IntelBrief`, never recomputes regime | One market call, one chokepoint |
| 2 | Coverage widens **before** narrative is written | Narrating a 6-signal view in 12-signal language is false confidence |
| 3 | Hard quorum rule with visible refusal | Prevents confident output on broken data |
| 4 | `ScanGate.check()` remains the only hard gate | Layer contract #2 — conductor *reports* gates, never opens one |
| 5 | Deterministic, no LLM in the engine path | Same inputs → byte-identical brief, or it cannot be backtested |

Condition 5 matters most and is the easiest to get wrong. If the narrative is generated by a model at runtime, the brief stops being a reproducible artefact and cannot be validated against `research/opportunity_layer1.py`. **Template-fill from typed fields, not free generation.**

---

## 3. Components it should have

| # | Component | Responsibility |
|---|---|---|
| 1 | **Adapter registry** | Declarative map monitor → adapter → snapshot, with a dependency order (`soldiers` needs `generals.score_5d`, so generals loads first) |
| 2 | **Freshness ledger** | Per-source `LIVE / STALE / NO_DATA` + age. Reuses the v0.1 `MonitorSignal` idea — it was the right instinct |
| 3 | **Quorum guard** | Refuses a regime call below N usable signals; emits `INSUFFICIENT_DATA`, not a number |
| 4 | **Reconciliation engine** | The actual new value. Named, testable cross-module divergence rules (§4) |
| 5 | **Rotation map** | Where dollars enter / leave, from sector rotation + ETF + value + soldiers RS |
| 6 | **Opportunity ledger** | Breakout/movers/trendline merged into A / B / trap tiers with the confirming and dissenting source named per ticker |
| 7 | **Strategy readiness matrix** | Per-ticker CSP / CC / LEAP allow-block with the *blocking source* attributed |
| 8 | **Narrative renderer** | Deterministic template fill → mentor brief |
| 9 | **Heartbeat + archive** | Timestamped JSON of every brief, so today's call can be scored later |

---

## 4. Reconciliation rules — where the value actually is

Single modules already speak. The conductor's only unique job is **what it means when two disagree.** Candidate rule set (each must be a named, unit-tested function):

| Rule | Condition | Meaning | Action |
|---|---|---|---|
| `R1_NARROW_DISTRIBUTION` | Generals strong **AND** Soldiers weak | Leadership narrowing; late-cycle | Cap LEAP sizing |
| `R2_SELLING_INTO_STRENGTH` | Index near highs **AND** distribution-day count rising | Institutions exiting into retail bid | Downgrade composite |
| `R3_DEFENSIVE_ROTATION` | Rotation into XLU/XLP/XLV **AND** breadth narrowing | Risk-off underneath a flat tape | Downgrade composite |
| `R4_TRAP_RICH` | Breakout A-tier count high **AND** `rally_breadth = EXTREME_NARROW` | Breakouts firing without participation | Demote all breakouts one tier |
| `R5_SCOUT_LEAD` | Scouts roll over **AND** Generals still healthy | Early warning — speculative appetite turning first | Raise a watch flag, no gate change |
| `R6_IV_SLEEVE_CONFLICT` | CSP wants high IVR **AND** LEAP wants low IVP, both gates open | Structural sleeve conflict | State which sleeve owns capital today |

`R6` is the one your current stack cannot express at all: CSP and LEAP scanners optimise opposite IV conditions and nothing arbitrates between them. **That is a real decision you are currently making by hand every day.**

---

## 5. Pros and cons

### Pros

- **Kills contradiction.** One published market call instead of 21 monitors that must be read in sequence and reconciled mentally.
- **Closes the lateness gap.** Accumulation, soldiers-divergence and scouts are early signals; adding them moves the engine from confirming to anticipating.
- **Makes the daily read repeatable** — your stated goal. A template-filled brief is the same shape every day, so you learn to read it in 60 seconds.
- **Backtestable.** Deterministic briefs archived as JSON can be scored against forward returns via `research/`.
- **Attribution.** "LEAP blocked by: `generals.leap_gate=CLOSED`" teaches you *why*, which a gate boolean never does.
- **Low architectural risk** if it consumes `IntelBrief` — no new chokepoint, no layer violation.

### Cons

- **Third-conductor risk.** The single biggest failure mode. Mitigated only by discipline: conductor **must not** own a scoring function.
- **Correlated inputs inflate confidence.** Generals, MegaCapTech, Soldiers, Scouts and Breakout all load off the same mega-cap tape. Twelve signals that agree may be one signal counted twelve times. **Weights must be set on measured correlation, not intuition.**
- **Two monitors need surgery first.** `etf_monitor` and `value_stocks` are report-shaped — per-ticker `analyse_*()` + `group_score()` + `run_once(send_email)`, no state object. Each needs a `get_*_state(router)` extraction. `etf_monitor._fetch` also depends on a module-global `_router` and falls back to a direct `yfinance` call, which is fragile under an adapter.
- **Weight-fiddling is overfitting.** Twelve tunable weights against one market history is a curve-fitting machine. Freeze weights, validate out-of-sample, or the score is decoration.
- **Narrative invites false confidence.** Well-written prose reads as conviction. Every claim in the brief must carry its source and freshness, or it becomes a story you believe.
- **Maintenance surface.** 12 adapters × schema drift in 21 monitors. Contract tests are not optional.

### The honest risk

The conductor makes a mega-cap-weighted, possibly-late signal *feel* like a complete market view, published in confident mentor prose. **That combination is more dangerous than the current fragmented state**, where the fragmentation itself forces you to think. Guard with: quorum refusal, source attribution on every line, a mandatory dissent section, and measured correlation weights.

---

## 6. Recommended build — sequenced

| Phase | Work | Output |
|---|---|---|
| **0** | Delete or archive `intelligence/master_intelligence.txt`; harvest `mentor_rules.yaml` thresholds into `settings.yaml` | One conductor lineage, not two |
| **1** | 6 adapters for the ready monitors: breadth, accumulation, sector_rotation, soldiers, scouts, movers. Extend `IntelBrief` with the new snapshot fields (defaults, additive) | Coverage 6 → 12 |
| **2** | Add quorum guard + `INSUFFICIENT_DATA` regime to `_composite_regime()` | Fixes defect §1.3.1 |
| **3** | Re-weight on **measured** correlation between the 12 signals; freeze in `settings.yaml` | Honest composite |
| **4** | `master_conductor.py` — reconciliation rules R1–R6 + rotation map + opportunity ledger + readiness matrix | The conductor |
| **5** | Narrative renderer → mentor template; JSON archive per run | The daily brief |
| **6** | `get_etf_state()` / `get_value_state()` extractions; adapters 13–14 | Full coverage |

Phases 1–2 alone deliver most of the value. **Do not start at Phase 4.**

---

## 7. Files in this branch

| File | What it is |
|---|---|
| `MASTER_CONDUCTOR_ASSESSMENT.md` | This document |
| `master_conductor_skeleton.py` | Phase-4 skeleton. Reference design — not wired, not importable inside this repo (it targets the ABRS engine repo) |
| `daily_brief_template.md` | Mentor-style daily report template, keyed to skeleton fields |

**These files belong in the ABRS engine repo** (`intelligence/`, `docs/`). They are committed here because this branch is in `schwabdev`, which is the broker SDK dependency — not the engine. Port them before wiring.
