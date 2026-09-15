# ABRS Daily Brief — Mentor Template

Fill-template for `reporting/conductor_brief_html.py`. Every `{field}` maps to a
`ConductorBrief` attribute in `master_conductor_skeleton.py`. Nothing in this
template is written free-hand at runtime — it is deterministic fill, so two runs
on the same `IntelBrief` produce the same brief.

**Rules for whoever renders this:**

1. **Every claim names its source.** No line ships without a module behind it.
2. **Never render a missing value as a number.** `NO_DATA` prints as `NO DATA`, never `0`.
3. **Dissent is mandatory.** If §7 is empty the brief is wrong, not clean.
4. **Paper is not live.** When `run_mode != "live"`, §9 renders as observations, and `is_actionable=False` is shown, not hidden.
5. **Below quorum, print §0 and stop.** Do not render §2–§9 from a partial view.

---

## §0 — REFUSAL BLOCK *(renders only when `quorum_met == False`; suppresses everything below)*

> ### REGIME CALL WITHHELD
> **{refusal_reason}**
> Sources live: **{sources_live}/{sources_total}**
>
> | Source | State | Age | Note |
> |---|---|---|---|
> | {health[].source} | {health[].freshness} | {health[].age_minutes}m | {health[].note} |
>
> Do not trade off this brief. Fix the adapters and re-run.

---

## §1 — REGIME SUMMARY

**{regime} — {composite_score}/100**
{headline}

- Run mode: **{run_mode}** · Built: {timestamp_utc} · From IntelBrief {intel_brief_ref}
- Data integrity: **{sources_live}/{sources_total} live**

> Regime is mirrored from `intel_brief`. The conductor never recomputes it.
> If this number disagrees with the intel brief, the build is broken.

---

## §2 — RISK GATES

| Sleeve | Status | Sizing cap | Blocking source |
|---|---|---|---|
| CSP | {csp_gate.status} | {csp_gate.sizing_cap} | {csp_gate.reason} |
| CC | {cc_gate.status} | {cc_gate.sizing_cap} | {cc_gate.reason} |
| LEAP | {leap_gate.status} | {leap_gate.sizing_cap} | {leap_gate.reason} |
| WHEEL | {wheel_gate.status} | {wheel_gate.sizing_cap} | {wheel_gate.reason} |

**Conductor tightening applied:** {reconciliations[].action where verdict == DIVERGES}

> Caps are monotonic. The conductor can only reduce what `ScanGate` allowed.
> A cap here that exceeds the gate cap is a bug — file it, do not trade it.

---

## §3 — LEADERSHIP & ROTATION

**Dollars in**

| Sector / cohort | Strength | Evidence |
|---|---|---|
| {flows_in[].name} | {flows_in[].strength} | {flows_in[].evidence} |

**Dollars out**

| Sector / cohort | Strength | Evidence |
|---|---|---|
| {flows_out[].name} | {flows_out[].strength} | {flows_out[].evidence} |

**Generals vs Soldiers vs Scouts**

- Generals: {generals.score_5d}/100 — gate {generals.leap_gate} — broken: {generals.broken_names}
- Soldiers: {soldiers.sss}/100 — divergence vs Generals: {R1 gap}
- Scouts: {scouts.score} — *scouts turn before generals; treat a scout roll-over as an early warning, not a confirmation*

---

## §4 — BREADTH & INTERNALS

| Gauge | Reading | Source |
|---|---|---|
| Sectors >50% above 20-DMA | {rally_breadth.sectors_above_50pct}/{rally_breadth.sectors_total} | rally_breadth |
| Concentration gap | {rally_breadth.concentration_gap_pp}pp | rally_breadth |
| Distribution days (25 sess.) | {accumulation.distribution_days} | accumulation |
| Breadth composite | {breadth.composite} | breadth_monitor |
| VIX / zone | {regime_context.vix} / zone {vix_zone} | regime_signal |
| Recovery stage | {recovery_pulse.stage} | recovery_pulse |

---

## §5 — CROSS-MODULE RECONCILIATION *(the part single modules cannot tell you)*

| Rule | Verdict | Finding | Implication | Action |
|---|---|---|---|---|
| {reconciliations[].rule_id} | {reconciliations[].verdict} | {reconciliations[].finding} | {reconciliations[].implication} | {reconciliations[].action} |

> `UNAVAILABLE` ≠ `NEUTRAL`. "Cannot tell" and "nothing wrong" are different
> states and must never collapse into one row.

---

## §6 — BREAKOUT & OPPORTUNITY MAP

| Ticker | Tier | Dir | Catalyst | Vol | Coil | Confirming | Dissenting |
|---|---|---|---|---|---|---|---|
| {opportunities[].ticker} | {opportunities[].tier} | {opportunities[].direction} | {has_catalyst} | {volume_confirmed} | {compressed} | {confirming} | {dissenting} |

**Tier demotion active:** {true when R4_TRAP_RICH fired}

---

## §7 — TOP DANGERS *(never empty — if it is, the brief did not run)*

{dangers[]}

**Avoid today**

{avoid[]}

---

## §8 — STRATEGY READINESS

| Ticker | Sleeve | Status | Cap | Blocking source | Notes |
|---|---|---|---|---|---|
| {readiness[].ticker} | {readiness[].strategy} | {readiness[].status} | {readiness[].sizing_cap} | {readiness[].blocking_source} | {readiness[].notes} |

**CC note:** CC readiness requires ≥100 held shares. Rows render from
`PortfolioState` at render time, not from the conductor — the conductor stays
free of position state.

---

## §9 — MENTOR COMMENTARY

**The pulse**
{pulse}

**Watch**
{watch[]}

**What today is not:** *(one line stating the thing the data cannot tell you —
the most under-used line in the brief. Example: "Breadth is measured on 11
sector proxies, not the full tape; a narrow reading is a hypothesis, not a
count.")*

---

## §10 — ACTIONABLE NEXT STEPS

{actions[]} *(3–5, sleeve-ordered CSP → CC → LEAP)*

> In `dev` / `paper` these are observations. `IntelCandidate.is_actionable` is
> `False` outside live mode and must be rendered, not hidden.

---

## §11 — PROVENANCE

| | |
|---|---|
| Run mode | {run_mode} |
| Built (UTC) | {timestamp_utc} |
| From IntelBrief | {intel_brief_ref} |
| Sources live | {sources_live}/{sources_total} |
| Rules fired | {len(reconciliations)} |
| Archived | `data_store/conductor_briefs/conductor_{stamp}.json` |

---

## How to read this in 60 seconds

1. **§1** — is the regime call real, or withheld?
2. **§2** — which sleeves are open, and what capped them?
3. **§5** — did anything diverge? Divergence is the whole reason this document exists.
4. **§7** — what am I avoiding today?
5. **§10** — the 3–5 things.

Everything else is evidence for those five. If you find yourself reading §3 and
§4 before §5, you are reading it as a report instead of a decision.

---

## Voice

- Flat and unemotional. No hedging, no enthusiasm.
- Numbers carry units and sources. `Generals 62/100` not `generals look strong`.
- Never write "should", "likely to", or "poised" about price.
- Dissent gets the same font as conviction.
- If a section has nothing to say, print `no signal` — never pad it.
