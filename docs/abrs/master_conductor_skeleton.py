"""
intelligence/master_conductor.py  —  SKELETON (Phase 4)
==============================================================================
ABRS — Master Intelligence Conductor

WHAT THIS IS
------------
The *narrative and reconciliation* layer that sits ON TOP of `intel_brief.py`.

It does NOT compute a regime. `intel_brief.build_intel_brief()` already does
that (composite 0-100, five buckets, most-restrictive-wins gates). Duplicating
that logic here creates a second market call that can contradict the first.

This module answers the one question `intel_brief` does not:
    "These twelve signals disagree. What does the disagreement MEAN,
     and which sleeve owns capital today?"

WHERE IT SITS
-------------
    Layer 2a  monitors/          get_*_state(router)  -> native state objects
    Layer 2b  adapters/          -> frozen *Snapshot dataclasses
    Layer 2c  intel_brief.py     -> IntelBrief  (regime + gates + candidates)
    Layer 2d  master_conductor   -> ConductorBrief  (THIS FILE)
    Layer 5   reporting/         -> HTML / email

ARCHITECTURE CONTRACTS HONOURED
-------------------------------
  1. Data flows downward only. Reads Layer 2 outputs. Never imports strategy/,
     execution/ or reporting/.
  2. ScanGate.check() is the single regime chokepoint. This module READS gate
     decisions off IntelBrief. It never opens a gate, never widens a cap.
     It may only ever make a gate MORE restrictive.
  3. get_mode() is the only source of run mode — read off IntelBrief.run_mode.
  4. No hardcoded dollar amounts. Sizing is a fraction; dollars come from
     PortfolioState @property at render time.
  5. No hardcoded VIX levels. All thresholds from config/yaml/settings.yaml
     under `master_conductor:`.
  6. DETERMINISTIC. No LLM call, no network, no clock-dependent branch beyond
     the timestamp field. Same IntelBrief in -> byte-identical ConductorBrief
     out. This is what makes the brief backtestable.

PREREQUISITES (do not build this file first)
--------------------------------------------
  Phase 1: adapters for breadth / accumulation / sector_rotation / soldiers /
           scouts / movers; IntelBrief extended with those snapshot fields.
  Phase 2: quorum guard in intel_brief._composite_regime().
  Phase 3: correlation-measured regime weights frozen in settings.yaml.

  Building this module against a 6-adapter IntelBrief produces confident prose
  over a mega-cap-only view. That is the failure mode this design exists to
  prevent.

MARKERS
-------
  WIRE:       signature confirmed against source at 99e5f0f — safe to call.
  WIRE-TODO:  field names NOT verified; confirm against the monitor's state
              dataclass before removing the guard.

Author: ABRS — Master Conductor skeleton — 2026-09-15
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

# WIRE: verified present at intelligence/intel_brief_types.py
from intelligence.intel_brief_types import (
    IntelBrief,
    IntelCandidate,
    StrategyGate,
    CompositeRegime,
    GateStatus,
)

_log = logging.getLogger("master_conductor")


# =============================================================================
#  0.  CONTRACTS
# =============================================================================

Freshness = Literal["LIVE", "STALE", "NO_DATA"]

#: Regime label used when quorum is not met. Deliberately NOT a CompositeRegime
#: value — a data failure is not a market state and must never be rendered as one.
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

Verdict = Literal["CONFIRMS", "DIVERGES", "NEUTRAL", "UNAVAILABLE"]


@dataclass(frozen=True)
class SourceHealth:
    """Freshness ledger entry — one per adapter attempted."""
    source: str                      # "soldiers", "accumulation", ...
    freshness: Freshness
    age_minutes: Optional[int]       # None when NO_DATA
    note: str = ""                   # failure reason when not LIVE

    @property
    def usable(self) -> bool:
        return self.freshness != "NO_DATA"


@dataclass(frozen=True)
class Reconciliation:
    """One fired cross-module rule. The core output of this module."""
    rule_id: str                     # "R1_NARROW_DISTRIBUTION"
    verdict: Verdict
    sources: tuple[str, ...]         # every source the rule read
    finding: str                     # one sentence, <= 25 words
    implication: str                 # what it means for capital
    action: str                      # "cap LEAP sizing at 0.50" | "watch only"
    severity: int                    # 1 = note, 2 = caution, 3 = act now


@dataclass(frozen=True)
class FlowEntry:
    """One leg of the rotation map."""
    name: str                        # sector / ETF / cohort
    direction: Literal["INFLOW", "OUTFLOW", "FLAT"]
    strength: float                  # 0.0-1.0, normalised RS
    evidence: tuple[str, ...]        # "sector_rotation: XLU rs_21d=+4.1pp"


@dataclass(frozen=True)
class OpportunityEntry:
    """Merged breakout / mover / trendline view of one ticker."""
    ticker: str
    tier: Literal["A", "B", "TRAP", "WATCH"]
    direction: Literal["UP", "DOWN", "NONE"]
    confirming: tuple[str, ...]      # sources that support it
    dissenting: tuple[str, ...]      # sources that contradict it — never empty
                                     # by convention; write ("none",) explicitly
    has_catalyst: bool
    volume_confirmed: bool
    compressed: bool                 # BB width in lowest decile


@dataclass(frozen=True)
class ReadinessEntry:
    """Per-ticker, per-strategy verdict with the BLOCKING SOURCE named."""
    ticker: str
    strategy: Literal["CSP", "CC", "LEAP"]
    status: GateStatus               # OPEN / REDUCED / CLOSED
    blocking_source: Optional[str]   # "generals.leap_gate=CLOSED"; None if OPEN
    sizing_cap: float                # 0.0-1.0 fraction
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ConductorBrief:
    """
    Master synthesis object. Never raises — on total failure returns a brief
    with regime=INSUFFICIENT_DATA and a populated `refusal_reason`.

    Travels Layer 2d -> Layer 5 only.
    """
    timestamp_utc: str
    run_mode: Literal["dev", "paper", "live"]

    # --- regime: MIRRORED from IntelBrief, never recomputed -----------------
    regime: str                      # CompositeRegime value, or INSUFFICIENT_DATA
    composite_score: Optional[int]   # None when quorum not met
    headline: str
    refusal_reason: Optional[str]    # populated iff regime == INSUFFICIENT_DATA

    # --- data integrity ------------------------------------------------------
    health: tuple[SourceHealth, ...]
    sources_live: int
    sources_total: int
    quorum_met: bool

    # --- synthesis -----------------------------------------------------------
    reconciliations: tuple[Reconciliation, ...]
    flows_in: tuple[FlowEntry, ...]
    flows_out: tuple[FlowEntry, ...]
    opportunities: tuple[OpportunityEntry, ...]
    readiness: tuple[ReadinessEntry, ...]

    # --- mentor narrative (deterministic template fill) ----------------------
    pulse: str                       # 2-4 sentences, the true market read
    dangers: tuple[str, ...]
    avoid: tuple[str, ...]
    watch: tuple[str, ...]
    actions: tuple[str, ...]         # 3-5 items, CSP/CC/LEAP

    # --- provenance ----------------------------------------------------------
    intel_brief_ref: str             # IntelBrief.timestamp_utc it was built from


# =============================================================================
#  1.  ENTRY POINT
# =============================================================================

def build_conductor_brief(brief: IntelBrief, config) -> ConductorBrief:
    """
    Build the master conductor brief from an already-built IntelBrief.

    Parameters
    ----------
    brief  : IntelBrief from intelligence.intel_brief.build_intel_brief()
             WIRE: verified — build_intel_brief(router, config) -> IntelBrief
    config : EngineConfig from config_loader.load_config()

    Returns
    -------
    ConductorBrief — always valid, never raises.

    Note the signature: this takes an IntelBrief, NOT a FeedRouter. That is
    deliberate and load-bearing. A conductor that takes a router can fetch its
    own data, and a conductor that can fetch its own data will eventually
    compute its own regime. Keep the dependency narrow.
    """
    ts = datetime.now(timezone.utc).isoformat()
    cfg = _conductor_config(config)

    try:
        health = _build_health_ledger(brief, cfg)
        live = sum(1 for h in health if h.freshness == "LIVE")
        total = len(health)

        quorum_min = int(cfg.get("quorum_min_sources", 8))
        quorum_met = live >= quorum_min

        # --- Quorum guard ---------------------------------------------------
        # Below quorum we refuse to restate the regime. intel_brief
        # renormalises weight across surviving adapters, which makes a
        # 2-of-12 score look exactly like a 12-of-12 score. The conductor is
        # the layer that makes that visible instead of smoothing it over.
        if not quorum_met:
            return _refusal_brief(
                ts, brief, health, live, total, quorum_min,
                reason=(
                    f"Quorum not met: {live}/{total} sources LIVE, "
                    f"minimum {quorum_min}. Regime call withheld."
                ),
            )

        recs = _run_reconciliation_rules(brief, cfg)
        flows_in, flows_out = _build_rotation_map(brief, cfg)
        opps = _build_opportunity_ledger(brief, recs, cfg)
        readiness = _build_readiness_matrix(brief, recs, cfg)

        narrative = _compose_narrative(brief, recs, flows_in, flows_out,
                                       opps, readiness, cfg)

        cb = ConductorBrief(
            timestamp_utc=ts,
            run_mode=brief.run_mode,
            regime=brief.composite_regime,          # MIRRORED, not recomputed
            composite_score=brief.composite_score,
            headline=brief.headline,
            refusal_reason=None,
            health=tuple(health),
            sources_live=live,
            sources_total=total,
            quorum_met=True,
            reconciliations=tuple(recs),
            flows_in=tuple(flows_in),
            flows_out=tuple(flows_out),
            opportunities=tuple(opps),
            readiness=tuple(readiness),
            intel_brief_ref=brief.timestamp_utc,
            **narrative,
        )
        _archive(cb, cfg)
        return cb

    except Exception as exc:                      # never propagate to Layer 5
        _log.exception("master_conductor: unhandled failure")
        return _refusal_brief(
            ts, brief, health=[], live=0, total=0, quorum_min=0,
            reason=f"Conductor failure: {type(exc).__name__}: {exc}",
        )


# =============================================================================
#  2.  FRESHNESS LEDGER
# =============================================================================

#: Adapter load order. Dependencies first — soldiers_adapter needs
#: generals.score_5d to compute its divergence matrix.
#: WIRE: get_soldiers_state(router, generals_sec5d=None) — confirmed at
#:       intelligence/monitors/soldiers_monitor.py:390
SOURCE_ORDER: tuple[str, ...] = (
    # existing 6
    "generals", "mega_cap_tech", "trendline", "rally_breadth",
    "recovery_pulse", "breakout",
    # Phase 1 additions
    "breadth", "accumulation", "sector_rotation", "soldiers",
    "scouts", "movers",
    # Phase 6 additions
    "etf", "value",
)


def _build_health_ledger(brief: IntelBrief, cfg: dict) -> list[SourceHealth]:
    """
    One SourceHealth per source. A snapshot is None when its adapter raised
    AdapterDataUnavailable — that is NO_DATA, not a zero.

    WIRE-TODO: `staleness_minutes` is not currently carried on the snapshot
    dataclasses. Either add an `as_of_utc` field to each *Snapshot in Phase 1
    (preferred — it makes STALE detectable) or this function can only ever
    emit LIVE / NO_DATA and the STALE branch is dead code.
    """
    out: list[SourceHealth] = []
    stale_after = int(cfg.get("stale_after_minutes", 90))

    for name in SOURCE_ORDER:
        snap = getattr(brief, name, None)
        if snap is None:
            out.append(SourceHealth(name, "NO_DATA", None,
                                    "adapter unavailable or not yet built"))
            continue

        age = _snapshot_age_minutes(snap)          # WIRE-TODO
        if age is None:
            out.append(SourceHealth(name, "LIVE", None, "age unknown"))
        elif age > stale_after:
            out.append(SourceHealth(name, "STALE", age,
                                    f"older than {stale_after}min tolerance"))
        else:
            out.append(SourceHealth(name, "LIVE", age))
    return out


def _snapshot_age_minutes(snap: Any) -> Optional[int]:
    """WIRE-TODO: requires `as_of_utc: str` on every *Snapshot dataclass."""
    as_of = getattr(snap, "as_of_utc", None)
    if not as_of:
        return None
    try:
        then = datetime.fromisoformat(as_of)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    except (ValueError, TypeError):
        return None


# =============================================================================
#  3.  RECONCILIATION ENGINE  —  the only genuinely new logic in this module
# =============================================================================
#
# Each rule is a pure function (IntelBrief, cfg) -> Optional[Reconciliation].
# Pure so it is unit-testable against a synthetic IntelBrief with no router,
# no network and no data_store. Every rule needs a test with a fixture that
# fires it and a fixture that does not.
#
# A rule returns None when it does not fire. A rule returns a Reconciliation
# with verdict="UNAVAILABLE" when a source it needs is missing — silence and
# "cannot tell" are different states and the brief must distinguish them.
# =============================================================================


def _rule_r1_narrow_distribution(brief: IntelBrief, cfg: dict) -> Optional[Reconciliation]:
    """
    R1 — Generals strong while Soldiers weak.

    Leadership narrowing to mega-caps. Historically a late-cycle tell: the
    index holds up on 5 names while the broad growth cohort is already
    distributing. Caps LEAP sizing; does not close the gate.

    WIRE-TODO: SoldiersState field names unconfirmed. Expected `sss` (soldiers
    strength score) per soldiers_monitor._build_divergence(sss, generals_sec5d).
    """
    gen = getattr(brief, "generals", None)
    sol = getattr(brief, "soldiers", None)
    if gen is None or sol is None:
        return Reconciliation(
            "R1_NARROW_DISTRIBUTION", "UNAVAILABLE", ("generals", "soldiers"),
            "Cannot assess leadership breadth — a required source is missing.",
            "Narrowing is undetectable this run.", "no action", 1,
        )

    gen_score = float(gen.score_5d)                       # WIRE: verified field
    sol_score = float(getattr(sol, "sss", 0.0))           # WIRE-TODO
    gap = gen_score - sol_score
    threshold = float(cfg.get("r1_divergence_gap_pp", 25.0))

    if gen_score >= float(cfg.get("r1_generals_strong_min", 60)) and gap >= threshold:
        return Reconciliation(
            "R1_NARROW_DISTRIBUTION", "DIVERGES", ("generals", "soldiers"),
            f"Generals {gen_score:.0f} vs Soldiers {sol_score:.0f} — gap {gap:.0f}pp.",
            "Leadership is narrowing to mega-caps; broad risk appetite is fading.",
            f"cap LEAP sizing at {cfg.get('r1_leap_cap', 0.50)}",
            severity=3,
        )
    return None


def _rule_r2_selling_into_strength(brief: IntelBrief, cfg: dict) -> Optional[Reconciliation]:
    """
    R2 — Index near highs while distribution days accumulate.

    IBD-style: institutions exiting into a retail bid. The single most useful
    signal the current 6-adapter composite is blind to.

    WIRE-TODO: AccumulationState field names unconfirmed. See
    accumulation_monitor._composite_signal() -> CompositeSignal.
    """
    acc = getattr(brief, "accumulation", None)
    if acc is None:
        return Reconciliation(
            "R2_SELLING_INTO_STRENGTH", "UNAVAILABLE", ("accumulation",),
            "Accumulation/distribution unavailable.",
            "Institutional footprint unknown.", "no action", 1,
        )

    dist_days = int(getattr(acc, "distribution_days", 0))   # WIRE-TODO
    max_days = int(cfg.get("r2_distribution_days_max", 4))
    if dist_days >= max_days and brief.composite_score is not None \
            and brief.composite_score >= int(cfg.get("r2_score_min", 60)):
        return Reconciliation(
            "R2_SELLING_INTO_STRENGTH", "DIVERGES", ("accumulation", "intel_brief"),
            f"{dist_days} distribution days in 25 sessions with composite "
            f"{brief.composite_score}/100.",
            "Institutions are selling into strength; the score is flattered by price.",
            f"downgrade composite by {cfg.get('r2_penalty', 10)} in the narrative",
            severity=3,
        )
    return None


def _rule_r4_trap_rich(brief: IntelBrief, cfg: dict) -> Optional[Reconciliation]:
    """
    R4 — Breakouts firing into narrow breadth.

    When rally_breadth is EXTREME_NARROW, breakouts have no participation
    behind them and the base rate of failure rises. Every breakout tier is
    demoted one step. This is the rule that stops the opportunity section
    reading like a buy list in a distribution tape.
    """
    bo = getattr(brief, "breakout", None)
    rb = getattr(brief, "rally_breadth", None)
    if bo is None or rb is None:
        return None

    a_count = len(bo.a_conviction)                  # WIRE: verified field
    if rb.verdict == "EXTREME_NARROW" and a_count >= int(cfg.get("r4_min_a", 3)):
        return Reconciliation(
            "R4_TRAP_RICH", "DIVERGES", ("breakout", "rally_breadth"),
            f"{a_count} A-tier breakouts with breadth EXTREME_NARROW "
            f"({rb.sectors_above_50pct}/{rb.sectors_total} sectors).",
            "Breakouts are firing without participation — elevated failure rate.",
            "demote every breakout one tier; A->B, B->WATCH",
            severity=3,
        )
    return None


def _rule_r6_iv_sleeve_conflict(brief: IntelBrief, cfg: dict) -> Optional[Reconciliation]:
    """
    R6 — CSP and LEAP want opposite IV conditions, both gates open.

    CSP sells premium and wants IVR high (settings.yaml csp: IVR >= 30).
    LEAP buys premium and wants IVP low (leap_base.ivp_max_for_preferred 15).
    Nothing in the current stack arbitrates when both gates are open — the
    operator does it by hand. This rule makes the call explicit and logged.
    """
    ctx = brief.regime_context
    ivr = getattr(ctx, "ivr", None)
    ivp = getattr(ctx, "ivp", None)                 # WIRE: RegimeContext fields
    if ivr is None or ivp is None:
        return None

    csp_open = brief.csp_gate.status in ("OPEN", "REDUCED")
    leap_open = brief.leap_gate.status in ("OPEN", "REDUCED")
    if not (csp_open and leap_open):
        return None

    ivr_rich = float(ivr) >= float(cfg.get("r6_ivr_rich_min", 50))
    owner = "CSP" if ivr_rich else "LEAP"
    return Reconciliation(
        "R6_IV_SLEEVE_CONFLICT", "DIVERGES", ("regime_context", "csp_gate", "leap_gate"),
        f"Both sleeves open with IVR={float(ivr):.0f} IVP={float(ivp):.0f}.",
        "Premium selling and premium buying are structurally opposed at this IV.",
        f"{owner} owns incremental capital today",
        severity=2,
    )


#: Rule registry. Order is display order in the brief.
#: R3_DEFENSIVE_ROTATION and R5_SCOUT_LEAD are specified in the assessment
#: document and belong here once sector_rotation / scouts adapters exist.
RULES: tuple[Callable[[IntelBrief, dict], Optional[Reconciliation]], ...] = (
    _rule_r1_narrow_distribution,
    _rule_r2_selling_into_strength,
    _rule_r4_trap_rich,
    _rule_r6_iv_sleeve_conflict,
)


def _run_reconciliation_rules(brief: IntelBrief, cfg: dict) -> list[Reconciliation]:
    """Run every rule. One rule raising must not lose the others."""
    out: list[Reconciliation] = []
    for rule in RULES:
        try:
            r = rule(brief, cfg)
            if r is not None:
                out.append(r)
        except Exception as exc:
            _log.warning("rule %s failed: %s", getattr(rule, "__name__", "?"), exc)
    # Most severe first — the operator reads top-down and may stop early.
    return sorted(out, key=lambda r: -r.severity)


# =============================================================================
#  4.  ROTATION MAP
# =============================================================================

def _build_rotation_map(brief: IntelBrief, cfg: dict
                        ) -> tuple[list[FlowEntry], list[FlowEntry]]:
    """
    Where dollars are entering and leaving.

    Inputs: sector_rotation (primary), etf (confirm), value (confirm),
    soldiers RS (risk-appetite leg).

    WIRE-TODO: SectorRotationState field names unconfirmed. See
    sector_rotation_monitor.py:363 get_sector_rotation_state(router).

    Design note: rank by relative strength, not absolute return. In a down
    tape everything is negative and absolute ranking produces an empty
    inflow list, which reads as "no rotation" when rotation is exactly what
    is happening.
    """
    rot = getattr(brief, "sector_rotation", None)
    if rot is None:
        return [], []

    sectors = list(getattr(rot, "sectors", []))     # WIRE-TODO
    top_n = int(cfg.get("rotation_top_n", 3))

    ranked = sorted(sectors, key=lambda s: getattr(s, "rs_21d", 0.0), reverse=True)

    flows_in = [
        FlowEntry(
            name=getattr(s, "name", "?"),
            direction="INFLOW",
            strength=_normalise_rs(getattr(s, "rs_21d", 0.0)),
            evidence=(f"sector_rotation: {getattr(s, 'name', '?')} "
                      f"rs_21d={getattr(s, 'rs_21d', 0.0):+.1f}pp",),
        )
        for s in ranked[:top_n]
    ]
    flows_out = [
        FlowEntry(
            name=getattr(s, "name", "?"),
            direction="OUTFLOW",
            strength=_normalise_rs(getattr(s, "rs_21d", 0.0)),
            evidence=(f"sector_rotation: {getattr(s, 'name', '?')} "
                      f"rs_21d={getattr(s, 'rs_21d', 0.0):+.1f}pp",),
        )
        for s in ranked[-top_n:]
    ]
    return flows_in, flows_out


def _normalise_rs(rs: float) -> float:
    """Map relative strength in pp to 0.0-1.0. Clamped, not scaled to extremes."""
    return max(0.0, min(1.0, (float(rs) + 10.0) / 20.0))


# =============================================================================
#  5.  OPPORTUNITY LEDGER
# =============================================================================

def _build_opportunity_ledger(brief: IntelBrief,
                              recs: list[Reconciliation],
                              cfg: dict) -> list[OpportunityEntry]:
    """
    Merge breakout + movers + trendline into one tiered ledger.

    Applies R4 demotion when it fired. The `dissenting` tuple must never be
    left empty by omission — if nothing contradicts the name, write
    ("none",) explicitly, so an empty tuple always means "not yet checked".
    """
    bo = getattr(brief, "breakout", None)
    if bo is None:
        return []

    demote = any(r.rule_id == "R4_TRAP_RICH" and r.verdict == "DIVERGES" for r in recs)

    def tier_for(base: str) -> str:
        if not demote:
            return base
        return {"A": "B", "B": "WATCH", "TRAP": "TRAP", "WATCH": "WATCH"}[base]

    out: list[OpportunityEntry] = []
    for t in bo.a_conviction:                        # WIRE: verified field
        out.append(OpportunityEntry(
            ticker=t, tier=tier_for("A"), direction="UP",
            confirming=("breakout: CONFIRMED + catalyst",),
            dissenting=("R4_TRAP_RICH demotion",) if demote else ("none",),
            has_catalyst=True, volume_confirmed=True, compressed=True,
        ))
    for t in bo.b_conviction:
        out.append(OpportunityEntry(
            ticker=t, tier=tier_for("B"), direction="UP",
            confirming=("breakout: CONFIRMED, no catalyst",),
            dissenting=("no catalyst",), has_catalyst=False,
            volume_confirmed=True, compressed=True,
        ))
    for t in bo.d_conviction:
        out.append(OpportunityEntry(
            ticker=t, tier="TRAP", direction="NONE",
            confirming=(), dissenting=("breakout: TRAP / low volume",),
            has_catalyst=False, volume_confirmed=False, compressed=False,
        ))
    # WIRE-TODO: merge movers_scanner MoverCandidate list and trendline
    # per-ticker state once those adapters exist.
    return out


# =============================================================================
#  6.  STRATEGY READINESS MATRIX
# =============================================================================

def _build_readiness_matrix(brief: IntelBrief,
                            recs: list[Reconciliation],
                            cfg: dict) -> list[ReadinessEntry]:
    """
    Per-ticker CSP / CC / LEAP verdict with the BLOCKING SOURCE attributed.

    Contract 2 enforcement lives here: `_apply_conductor_caps` may only ever
    reduce sizing or tighten status. If you ever find yourself widening a cap
    in this function, the design has failed.
    """
    out: list[ReadinessEntry] = []

    for cand in brief.leap_candidates:               # WIRE: verified field
        out.append(_readiness_from_candidate(cand, brief.leap_gate, recs, cfg))
    for cand in brief.csp_candidates:
        out.append(_readiness_from_candidate(cand, brief.csp_gate, recs, cfg))

    # WIRE-TODO: CC readiness needs the held-position list. cc_scanner requires
    # >= 100 shares, so candidates only exist against real holdings — source
    # from PortfolioState at render time, not here (this layer stays pure).
    return out


def _readiness_from_candidate(cand: IntelCandidate,
                              gate: StrategyGate,
                              recs: list[Reconciliation],
                              cfg: dict) -> ReadinessEntry:
    status, cap, notes = _apply_conductor_caps(gate, cand, recs, cfg)
    return ReadinessEntry(
        ticker=cand.ticker,
        strategy=cand.strategy,
        status=status,
        blocking_source=None if status == "OPEN" else gate.reason,
        sizing_cap=cap,
        notes=tuple(notes),
    )


def _apply_conductor_caps(gate: StrategyGate,
                          cand: IntelCandidate,
                          recs: list[Reconciliation],
                          cfg: dict) -> tuple[GateStatus, float, list[str]]:
    """
    Reconciliation-driven tightening. MONOTONIC — never loosens.

    The final min() is the contract-2 guarantee in code. Keep it, and keep a
    test that asserts the returned cap is <= gate.sizing_cap for every input.
    """
    cap = float(gate.sizing_cap)
    status: GateStatus = gate.status
    notes: list[str] = []

    for r in recs:
        if r.verdict != "DIVERGES":
            continue
        if r.rule_id == "R1_NARROW_DISTRIBUTION" and cand.strategy == "LEAP":
            cap = min(cap, float(cfg.get("r1_leap_cap", 0.50)))
            notes.append("R1: leadership narrowing — LEAP sizing capped")
        if r.rule_id == "R2_SELLING_INTO_STRENGTH":
            cap = min(cap, float(cfg.get("r2_cap", 0.50)))
            notes.append("R2: distribution underway — sizing capped")

    if cap <= 0.0:
        status = "CLOSED"
    elif cap < float(gate.sizing_cap):
        status = "REDUCED" if status == "OPEN" else status

    return status, min(cap, float(gate.sizing_cap)), notes


# =============================================================================
#  7.  NARRATIVE  —  deterministic template fill, NO generative call
# =============================================================================

def _compose_narrative(brief: IntelBrief,
                       recs: list[Reconciliation],
                       flows_in: list[FlowEntry],
                       flows_out: list[FlowEntry],
                       opps: list[OpportunityEntry],
                       readiness: list[ReadinessEntry],
                       cfg: dict) -> dict:
    """
    Build the mentor narrative fields by template fill from typed data.

    Every sentence must be traceable to a field. If a sentence cannot name its
    source, it does not belong in the brief — that is the line between
    synthesis and storytelling, and it is the whole reason this is template
    fill rather than a model call.

    Maps 1:1 onto docs/abrs/daily_brief_template.md.
    """
    acting = [r for r in recs if r.severity >= 3]

    pulse = _fill_pulse(brief, acting, flows_in, flows_out)

    dangers = tuple(f"{r.rule_id}: {r.finding}" for r in acting)
    avoid = tuple(
        f"{o.ticker} — {'; '.join(o.dissenting)}"
        for o in opps if o.tier == "TRAP"
    )
    watch = tuple(
        f"{o.ticker} — {'; '.join(o.confirming)}"
        for o in opps if o.tier in ("B", "WATCH")
    )
    actions = _fill_actions(brief, readiness, recs, cfg)

    return {
        "pulse": pulse,
        "dangers": dangers,
        "avoid": avoid,
        "watch": watch,
        "actions": actions,
    }


def _fill_pulse(brief: IntelBrief,
                acting: list[Reconciliation],
                flows_in: list[FlowEntry],
                flows_out: list[FlowEntry]) -> str:
    """2-4 sentences. Flat, unemotional, source-attributed."""
    parts = [
        f"Regime {brief.composite_regime} at {brief.composite_score}/100."
    ]
    if flows_in:
        parts.append("Dollars into " + ", ".join(f.name for f in flows_in) + ".")
    if flows_out:
        parts.append("Out of " + ", ".join(f.name for f in flows_out) + ".")
    if acting:
        parts.append(acting[0].implication)
    else:
        parts.append("No cross-module divergence fired — signals are aligned.")
    return " ".join(parts)


def _fill_actions(brief: IntelBrief,
                  readiness: list[ReadinessEntry],
                  recs: list[Reconciliation],
                  cfg: dict) -> tuple[str, ...]:
    """
    3-5 actionable items. Sleeve-ordered: CSP, CC, LEAP.

    In dev/paper these are observations. IntelCandidate.is_actionable is False
    outside live mode — the renderer must show that, or a paper brief reads
    like an order ticket.
    """
    out: list[str] = []
    for strat, gate in (("CSP", brief.csp_gate),
                        ("CC", brief.cc_gate),
                        ("LEAP", brief.leap_gate)):
        names = [r.ticker for r in readiness
                 if r.strategy == strat and r.status in ("OPEN", "REDUCED")]
        if gate.status == "CLOSED":
            out.append(f"{strat}: BLOCKED — {gate.reason}")
        elif names:
            out.append(f"{strat}: {gate.status} @ cap {gate.sizing_cap:.2f} — "
                       + ", ".join(names[:5]))
        else:
            out.append(f"{strat}: {gate.status}, no candidates cleared filters")

    owner = next((r for r in recs if r.rule_id == "R6_IV_SLEEVE_CONFLICT"), None)
    if owner is not None:
        out.append(f"Sleeve priority: {owner.action}")
    return tuple(out[:5])


# =============================================================================
#  8.  REFUSAL / PERSISTENCE / CONFIG
# =============================================================================

def _refusal_brief(ts: str, brief: IntelBrief, health: list[SourceHealth],
                   live: int, total: int, quorum_min: int,
                   reason: str) -> ConductorBrief:
    """
    A brief that declines to call the market.

    This is a first-class output, not an error path. Rendering it must look
    deliberate — "the engine withheld a call today" — never like a crash.
    """
    return ConductorBrief(
        timestamp_utc=ts,
        run_mode=getattr(brief, "run_mode", "dev"),
        regime=INSUFFICIENT_DATA,
        composite_score=None,
        headline="Regime call withheld — insufficient live sources.",
        refusal_reason=reason,
        health=tuple(health),
        sources_live=live,
        sources_total=total,
        quorum_met=False,
        reconciliations=(), flows_in=(), flows_out=(),
        opportunities=(), readiness=(),
        pulse=reason,
        dangers=("Data integrity failure — do not trade off this brief.",),
        avoid=(), watch=(),
        actions=("Investigate adapter failures before the next run.",),
        intel_brief_ref=getattr(brief, "timestamp_utc", ""),
    )


def _conductor_config(config) -> dict:
    """
    Read the `master_conductor:` block from settings.yaml.

    Contract 8: no threshold is hardcoded in this module. Every literal in the
    cfg.get() defaults above is a fallback for a missing key, not a setting.
    """
    try:
        return dict(getattr(config, "master_conductor", {}) or {})
    except Exception:
        return {}


def _archive(cb: ConductorBrief, cfg: dict) -> None:
    """
    Append the brief to a timestamped JSON archive.

    This is what makes the conductor backtestable: today's call, scored
    against forward returns in 60 days via research/. Without the archive the
    module is a nicer-looking report and nothing more.

    WIRE: use shared.data_paths for the root — never build a path from
    __file__ in a module that Layer 5 also reads.
    """
    try:
        from shared.data_paths import DATA_STORE_ROOT      # WIRE
        out_dir = Path(DATA_STORE_ROOT) / "conductor_briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = cb.timestamp_utc.replace(":", "").replace("-", "")[:15]
        (out_dir / f"conductor_{stamp}.json").write_text(
            json.dumps(asdict(cb), indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:
        _log.warning("conductor archive failed: %s", exc)


# =============================================================================
#  9.  TEST SURFACE — build these alongside, not after
# =============================================================================
#
#   tests/test_master_conductor.py
#     - each rule: one fixture that fires it, one that does not
#     - quorum: 7 LIVE of 12 -> INSUFFICIENT_DATA; 8 -> normal brief
#     - monotonicity: for every input, returned cap <= gate.sizing_cap
#     - determinism: same IntelBrief twice -> equal briefs (timestamp aside)
#     - total failure: every snapshot None -> valid refusal brief, no raise
#
# The monotonicity test is the important one. It is the executable form of
# layer contract 2, and it is the test that will catch the day someone adds a
# rule that "unlocks" LEAPs because breadth improved.
# =============================================================================
