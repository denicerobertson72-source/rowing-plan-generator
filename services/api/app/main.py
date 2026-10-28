"""API-first adapter around the preserved deterministic planning engine."""
from __future__ import annotations
import json
import hashlib
import logging
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from time import perf_counter
from traceback import extract_tb
from enum import Enum
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from rowing_plan import PLANNER_VERSION
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import PlanningConflict, generate_plan
from rowing_plan.validators import hard_constraint_errors, validate_profile
from rowing_plan.workbook import build_workbook
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.replacement_shadow import PlannerShadowComparison, compare_v2_with_v3
from .repositories import REPOSITORIES, profile_revision, public_profile, with_profile_revision
from .auth import current_user_id
from rowing_plan.training_load import load_summary, session_load_au
from rowing_plan.recurring_activities import normalize_recurring_schedule_for_planning, schedule_signature
from .schemas import ApiHealth, AthleteCreateRequest, AthleteResponse, AthleteUpdateRequest, OptionalActivityLogRequest, PlanGenerationRequest, PlanResponse, PrivateCheckInRequest, RacePostingRequest, RegenerateRequest, WeeklyOverrideRequest, WorkoutLogRequest

CONFIG = json.loads((ROOT / "config/defaults.json").read_text())
logger = logging.getLogger(__name__)
app = FastAPI(title="Rowing Plan API", version="0.4.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
allowed_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


class PlannerChoice(str, Enum):
    """Internal planner identity; this is deliberately not an API schema field."""

    V1 = "v1"
    V2 = "v2"


class PlannerRoutingError(RuntimeError):
    """A deterministic internal routing failure before any persistence occurs."""

    def __init__(self, reason_code: str, diagnostics: dict | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.diagnostics = dict(diagnostics or {})


_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
GENERATION_INPUT_FINGERPRINT_VERSION = "generation-input-v1"

# The source digest is intentionally part of the input contract.  It makes a
# deployed planner/scheduler algorithm change invalidate older PlanVersions
# even if an individual engine module has not yet introduced a separate
# version constant.
_PLANNER_SOURCE_FILES = {
    PlannerChoice.V1: (
        "scheduler.py", "periodization.py", "intensity.py", "power_profile.py",
        "recurring_activities.py", "session_archetypes.py", "session_selection.py",
        "session_selector.py", "load_transformations.py",
    ),
    PlannerChoice.V2: (
        "planner_v2.py", "scheduler_v2.py", "periodization.py", "intensity.py",
        "power_profile.py", "recurring_activities.py", "materialization_v2.py",
        "plan_assembly_v2.py", "session_archetypes.py", "session_selection.py",
        "session_selector.py",
    ),
}


def v2_planner_shadow_enabled() -> bool:
    """Permit diagnostic V2 shadow execution; never changes public routing."""

    return os.getenv("V2_PLANNER_SHADOW_ENABLED", "").strip().lower() in _TRUE_ENV_VALUES


def replacement_planner_shadow_enabled() -> bool:
    """Enable V3 comparison only; it never affects routing or persistence."""

    return os.getenv("REPLACEMENT_PLANNER_SHADOW_ENABLED", "").strip().lower() in _TRUE_ENV_VALUES


def v2_emergency_v1_rollback_enabled() -> bool:
    """Server-side emergency rollback; V2 is otherwise the production default."""

    return os.getenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", "").strip().lower() in _TRUE_ENV_VALUES


def default_planner_choice() -> PlannerChoice:
    return PlannerChoice.V1 if v2_emergency_v1_rollback_enabled() else PlannerChoice.V2


def resolve_authenticated_athlete_planner(*, internal_planner_choice: PlannerChoice | None = None) -> PlannerChoice:
    """Authenticated generation is V2-by-default and never cohort-gated."""

    return internal_planner_choice or default_planner_choice()


@dataclass(frozen=True)
class ShadowPlannerResult:
    attempted: bool
    success: bool
    failure_category: str | None = None
    failure_reason_code: str | None = None
    invariants: tuple[tuple[str, bool], ...] = ()
    target_statuses: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReplacementShadowResult:
    attempted: bool
    success: bool
    comparison: PlannerShadowComparison | None = None
    failure_category: str | None = None
    failure_reason_code: str | None = None
    timings_ms: tuple[tuple[str, float], ...] = ()


def compare_planner_invariants(v1_plan: dict, v2_plan: dict, *, locked_sessions=()) -> tuple[tuple[str, bool], ...]:
    """Compare only external hard facts; never rank intentionally different plans."""

    from rowing_plan.planner_v2 import validate_v2_plan_contract

    fields = {"date", "day", "session_id", "mode", "title", "band", "total_cardio_minutes", "phase", "structure"}
    v2_sessions = tuple(v2_plan.get("sessions", ()))
    v2_keys = {(item.get("date"), item.get("session_id"), item.get("mode")) for item in v2_sessions}
    v1_races = {(item.get("date"), item.get("session_id"), item.get("mode")) for item in v1_plan.get("sessions", ()) if item.get("session_id") == "RACE"}
    v1_private_dates = {item.get("date") for item in v1_plan.get("sessions", ()) if item.get("title") == "Private coaching"}
    v2_private_dates = {item.get("date") for item in v2_sessions if item.get("title") == "Private coaching"}
    v1_dates = tuple(item.get("date") for item in v1_plan.get("calendar_days", ()))
    v2_dates = tuple(item.get("date") for item in v2_plan.get("calendar_days", ()))
    rest_dates = {item.get("date") for item in v2_plan.get("calendar_days", ()) if item.get("designated_rest")}
    return (
        ("v2_contract_valid", validate_v2_plan_contract(v2_plan, authoritative_sessions=locked_sessions) is None),
        ("required_session_fields_complete", all(fields <= set(item) for item in v2_sessions)),
        ("external_session_keys_unique", len(v2_keys) == len(v2_sessions)),
        ("race_commitments_preserved", v1_races <= v2_keys),
        ("fixed_private_commitments_preserved", v1_private_dates <= v2_private_dates),
        ("completed_locked_sessions_preserved", all((item.get("date"), item.get("session_id"), item.get("mode")) in v2_keys for item in locked_sessions)),
        ("calendar_date_range_valid", bool(v2_dates) and (not v1_dates or (v1_dates[0], v1_dates[-1]) == (v2_dates[0], v2_dates[-1]))),
        ("designated_rest_calendar_only", not any(item.get("date") in rest_dates for item in v2_sessions)),
        ("no_rest_session_emitted", not any(item.get("session_id") == "REST" for item in v2_sessions)),
        ("materialization_complete", all(fields <= set(item) for item in v2_sessions)),
    )


def _shadow_target_statuses(v2_plan: dict) -> tuple[tuple[str, str], ...]:
    """Expose V2 target state descriptively, without comparing it to V1 totals."""

    records = []
    for window in v2_plan.get("v2_diagnostics", {}).get("rolling_targets", ()):
        if not isinstance(window, dict):
            continue
        suffix = window.get("window_end", "")
        strength = window.get("strength", {})
        if isinstance(strength, dict) and strength.get("status"):
            records.append((f"strength:{suffix}", str(strength["status"])))
        for target in window.get("rowing_targets", ()):
            if isinstance(target, dict) and target.get("category") and target.get("status"):
                records.append((f"{target['category']}:{suffix}", str(target["status"])))
    return tuple(sorted(records))


def run_v2_shadow(*, profile: dict, bands: list[dict], power: dict, locked_sessions: list[dict], v1_plan: dict) -> ShadowPlannerResult:
    """Build V2 read-only beside authoritative V1; no exception reaches users."""

    try:
        shadow_plan = execute_selected_planner(choice=PlannerChoice.V2, profile=profile, bands=bands, power=power, locked_sessions=locked_sessions)
        return ShadowPlannerResult(True, True, invariants=compare_planner_invariants(v1_plan, shadow_plan, locked_sessions=locked_sessions), target_statuses=_shadow_target_statuses(shadow_plan))
    except PlannerRoutingError as error:
        category = "plan_contract" if error.reason_code == "v2_plan_contract_failed" else "expected_v2_failure"
        return ShadowPlannerResult(True, False, category, error.reason_code)
    except Exception:
        return ShadowPlannerResult(True, False, "unexpected", "unexpected_v2_exception")


def run_replacement_shadow(*, profile: dict, bands: list[dict], power: dict, locked_sessions: list[dict],
                           v2_plan: dict, prior_plan: dict | None, history_actuals: dict[str, dict] | None,
                           planning_date: date) -> ReplacementShadowResult:
    """Run V3 beside authoritative V2 with identical normalized input facts.

    This function is deliberately exception-contained: callers must never lose
    a successful V2 response because a shadow candidate cannot be generated.
    """
    try:
        candidate = generate_plan_sequence_v3_candidate(
            profile, CONFIG, bands, power, planning_date=planning_date,
            prior_plan=prior_plan, history_actuals=history_actuals,
            locked_sessions=locked_sessions,
        )
        comparison_started = perf_counter()
        comparison = compare_v2_with_v3(v2_plan=v2_plan, candidate=candidate, planning_date=planning_date)
        timings = (*candidate.stage_timings_ms, ("comparison", round((perf_counter() - comparison_started) * 1000, 3)))
        return ReplacementShadowResult(True, candidate.success, comparison,
                                       None if candidate.success else "candidate_failure",
                                       candidate.failure_reason, timings)
    except Exception as error:  # shadow diagnostics must not change the live result
        logger.warning("planner_shadow v3_status=failed failure_category=unexpected failure_code=%s", type(error).__name__)
        return ReplacementShadowResult(True, False, None, "unexpected", type(error).__name__)


def log_replacement_shadow(result: ReplacementShadowResult) -> None:
    """Log only aggregate shadow facts; no profile or session payloads."""

    comparison = result.comparison
    logger.info(
        "planner_shadow v2_status=success v3_status=%s classification=%s promotion_blockers=%s "
        "needs_generated=%s needs_placed=%s needs_unplaced=%s materialization_failures=%s "
        "research_contract_failures=%s timings_ms=%s failure_category=%s failure_code=%s",
        "success" if result.success else "failed",
        comparison.classification if comparison else "unresolved",
        comparison.promotion_blockers if comparison else (),
        comparison.needs_generated if comparison else 0,
        comparison.needs_placed if comparison else 0,
        comparison.needs_unplaced if comparison else 0,
        comparison.materialization_failures if comparison else (),
        tuple(item.contract for item in comparison.research_contracts if item.status == "fail") if comparison else (),
        result.timings_ms, result.failure_category, result.failure_reason_code,
    )


def log_planner_generation(*, authoritative_planner: str, shadow: ShadowPlannerResult | None = None, fallback_used: bool = False, fallback_reason_code: str | None = None, failure_diagnostics: dict | None = None, mode: str | None = None, regeneration_skipped: bool = False) -> None:
    """Emit non-sensitive routing telemetry without serializing plans or profiles."""

    if shadow is None and not fallback_used and authoritative_planner == "v1" and mode is None:
        return
    logger.info("planner_generation mode=%s authoritative_planner=%s regeneration_skipped=%s shadow_planner=%s shadow_attempted=%s shadow_success=%s shadow_failure_category=%s shadow_failure_code=%s shadow_invariants=%s shadow_target_statuses=%s fallback_used=%s fallback_reason_code=%s failure_diagnostics=%s", mode, authoritative_planner, regeneration_skipped, "v2" if shadow else None, shadow.attempted if shadow else False, shadow.success if shadow else None, shadow.failure_category if shadow else None, shadow.failure_reason_code if shadow else None, shadow.invariants if shadow else (), shadow.target_statuses if shadow else (), fallback_used, fallback_reason_code, failure_diagnostics or {})


def _canonical_json(value) -> str:
    """Canonical JSON for deterministic, non-secret planner-input hashing."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=lambda item: item.isoformat() if isinstance(item, (date,)) else str(item))


def current_planning_date() -> date:
    """Single clock boundary for generation-input validity semantics."""

    return date.today()


def _planner_source_digest(choice: PlannerChoice) -> str:
    digest = hashlib.sha256()
    for filename in _PLANNER_SOURCE_FILES[choice]:
        path = ROOT / "rowing_plan" / filename
        digest.update(filename.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _user_placement_authority(plan: dict | None) -> tuple[dict, ...]:
    diagnostics = (plan or {}).get("v2_diagnostics")
    if not isinstance(diagnostics, dict):
        return ()
    authority = tuple(item for item in diagnostics.get("placement_authority", ()) if isinstance(item, dict) and (item.get("user_fixed") or item.get("override_id")))
    overrides = tuple(item for item in diagnostics.get("overrides", ()) if isinstance(item, dict))
    return tuple(sorted((dict(item) for item in authority + overrides), key=_canonical_json))


def generation_input_fingerprint(*, profile: dict, choice: PlannerChoice, locked_sessions: list[dict], actuals: dict[str, dict], weekly_overrides: list[dict], prior_plan: dict | None, as_of: date | None = None) -> dict:
    """Hash every currently consumed or authority-bearing generation input.

    The fingerprint deliberately excludes persistence IDs and timestamps that
    do not alter planning, while retaining actual payloads and override order
    because those records can alter the authoritative displayed/training state.
    """

    normalized = normalize_recurring_schedule_for_planning(profile)
    effective_profile = {key: value for key, value in normalized.items() if key != "_profile_revision"}
    payload = {
        "fingerprint_version": GENERATION_INPUT_FINGERPRINT_VERSION,
        "planner": choice.value,
        "planner_version": PLANNER_VERSION,
        "planner_source_digest": _planner_source_digest(choice),
        "config": CONFIG,
        "planning_date": (as_of or current_planning_date()).isoformat(),
        "profile": effective_profile,
        "locked_sessions": tuple(sorted((dict(item) for item in locked_sessions), key=_canonical_json)),
        "actuals": {key: actuals[key] for key in sorted(actuals)},
        "weekly_overrides": tuple(dict(item) for item in weekly_overrides),
        "user_placement_authority": _user_placement_authority(prior_plan),
    }
    return {
        "version": GENERATION_INPUT_FINGERPRINT_VERSION,
        "planner": choice.value,
        "planning_date": payload["planning_date"],
        "sha256": hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(),
    }


def _complete_plan_for_noop(plan: dict) -> bool:
    return bool(
        isinstance(plan, dict)
        and isinstance(plan.get("sessions"), list)
        and isinstance(plan.get("calendar_days"), list)
        and isinstance(plan.get("weekly_totals"), list)
        and plan.get("plan_version")
    )


def plan_matches_generation_inputs(record: dict | None, fingerprint: dict) -> bool:
    if not record or not _complete_plan_for_noop(record.get("plan", {})):
        return False
    stored = record["plan"].get("generation_input_fingerprint")
    return isinstance(stored, dict) and stored == fingerprint


def _authoritative_choice_for_plan(plan: dict, requested: PlannerChoice) -> PlannerChoice:
    routing = plan.get("planner_routing") if isinstance(plan.get("planner_routing"), dict) else {}
    if routing.get("final_planner") == "v1":
        return PlannerChoice.V1
    if isinstance(plan.get("v2_diagnostics"), dict) and plan["v2_diagnostics"].get("scheduler_version") == "v2":
        return PlannerChoice.V2
    return requested


def _log_generation_timing(*, planner: str, stage: str, started: float) -> None:
    logger.info("planner_generation_timing planner=%s stage=%s duration_ms=%.3f", planner, stage, (perf_counter() - started) * 1000)


def _session_key_for_generation(session: dict) -> str:
    return f'{session.get("date")}:{session.get("session_id")}:{session.get("mode")}'


def _generation_history(previous: dict | None) -> tuple[list[dict], dict[str, dict], tuple[str, ...]]:
    """Recover completed-history inputs carried through immutable versions."""

    if not previous:
        return [], {}, ()
    plan = previous.get("plan", {})
    context = plan.get("generation_input_context", {}) if isinstance(plan, dict) else {}
    inherited_locked = [dict(item) for item in context.get("locked_sessions", ()) if isinstance(item, dict)] if isinstance(context, dict) else []
    source_ids = [str(item) for item in context.get("actual_plan_ids", ()) if item] if isinstance(context, dict) else []
    source_ids.append(previous["plan_id"])
    actuals: dict[str, dict] = {}
    for plan_id in source_ids:
        for entry in REPOSITORIES.logs_for_plan(plan_id):
            if isinstance(entry.get("payload"), dict):
                actuals[entry["session_key"]] = entry["payload"]
    completed = {key for key, payload in actuals.items() if payload.get("status") == "completed"}
    newly_completed = [dict(session) for session in plan.get("sessions", ()) if _session_key_for_generation(session) in completed]
    optional_completed = [
        optional_activity_history_session(key, payload)
        for key, payload in actuals.items()
        if key in completed and key.startswith("actual:")
    ]
    locked_by_key = {_session_key_for_generation(item): item for item in inherited_locked + newly_completed + optional_completed}
    return list(locked_by_key.values()), actuals, tuple(dict.fromkeys(source_ids))


def resolve_planner_choice(internal_planner_choice: PlannerChoice | None = None) -> PlannerChoice:
    """Resolve explicit internal selection, otherwise the production default."""

    return internal_planner_choice or default_planner_choice()


def execute_selected_planner(*, choice: PlannerChoice, profile: dict, bands: list[dict], power: dict, locked_sessions: list[dict], prior_plan: dict | None = None, history_actuals: dict[str, dict] | None = None, planning_date: date | None = None) -> dict:
    """Return a complete in-memory plan. Persistence remains exclusively with callers.

    V2.8B will connect the complete V2 in-memory PlanVersion builder here. Any
    future fresh-plan fallback belongs outside this function and before save.
    """

    if choice is PlannerChoice.V1:
        return generate_plan(profile, CONFIG, bands, power, locked_sessions)
    if choice is PlannerChoice.V2:
        # Kept here rather than in the API route so V2.8B has the same shared
        # normalized input contract as V1 and still performs no persistence.
        from rowing_plan.planner_v2 import V2PlanningError, generate_plan_v2, validate_v2_plan_contract_details
        try:
            plan = generate_plan_v2(profile, CONFIG, bands, power, locked_sessions, prior_plan=prior_plan, history_actuals=history_actuals, planning_date=planning_date)
        except V2PlanningError as error:
            raise PlannerRoutingError(error.reason_code, error.diagnostics) from error
        validation_started = perf_counter()
        historical = []
        if prior_plan and planning_date:
            historical = [dict(item) for item in prior_plan.get("sessions", ()) if isinstance(item, dict) and isinstance(item.get("date"), str) and item["date"] < planning_date.isoformat()]
        # Preserve the same precedence used by V2 assembly: explicit locks
        # supersede an equal-key historical record, while history-only actuals
        # remain physiological context rather than planned-session authority.
        contract_authority = {
            _session_key_for_generation(item): (item, "historical_preserved_session")
            for item in historical if isinstance(item, dict)
        }
        contract_authority.update({
            _session_key_for_generation(item): (item, "serialized_locked_session")
            for item in locked_sessions if isinstance(item, dict)
        })
        contract_error = validate_v2_plan_contract_details(plan, authoritative_sessions=tuple(item for item, _ in contract_authority.values()))
        _log_generation_timing(planner="v2", stage="validation", started=validation_started)
        if contract_error:
            identity = contract_error.get("source_session_identity", {})
            key = f'{identity.get("date")}:{identity.get("session_id")}:{identity.get("mode")}'
            source_date = identity.get("date")
            recovery_dates = set(plan.get("v2_diagnostics", {}).get("post_race_recovery_dates", ()))
            contract_error = {
                **contract_error,
                "failure_stage": "validation",
                "authority_type": contract_authority.get(key, (None, contract_error.get("authority_type")))[1],
                "date_relation": "historical" if planning_date and source_date and source_date < planning_date.isoformat() else "current" if planning_date and source_date == planning_date.isoformat() else "future" if planning_date and source_date else None,
                "post_race_recovery_applies": bool(source_date in recovery_dates) if source_date else False,
            }
            raise PlannerRoutingError("v2_plan_contract_failed", contract_error) from ValueError(contract_error["reason_code"])
        return plan
    raise PlannerRoutingError("unknown_planner_choice")


_V2_FALLBACK_ELIGIBLE = frozenset({"v2_schedule_infeasible", "v2_materialization_failed", "v2_concrete_workout_failed"})


def _future_authority_dates(value) -> tuple[date, ...]:
    result = []
    for item in value or ():
        if not isinstance(item, dict):
            continue
        for raw in item.get("to_dates", ()) if isinstance(item.get("to_dates", ()), (list, tuple)) else ():
            try: result.append(date.fromisoformat(raw))
            except (TypeError, ValueError): pass
        raw = item.get("date")
        try: result.append(date.fromisoformat(raw))
        except (TypeError, ValueError): pass
    return tuple(result)


def v1_fallback_allowed(prior_plan: dict | None, *, as_of: date | None = None) -> bool:
    """Allow fallback only when no future persisted V2 authority would be lost."""

    if not prior_plan:
        return True
    diagnostics = prior_plan.get("v2_diagnostics") if isinstance(prior_plan, dict) else None
    if not isinstance(diagnostics, dict) or diagnostics.get("scheduler_version") != "v2":
        return True
    today = as_of or date.today()
    authority = tuple(item for item in diagnostics.get("placement_authority", ()) if isinstance(item, dict) and (item.get("user_fixed") or item.get("override_id")))
    overrides = tuple(item for item in diagnostics.get("overrides", ()) if isinstance(item, dict))
    return not any(day >= today for day in _future_authority_dates(authority + overrides))

@app.middleware("http")
async def prevent_dynamic_api_caching(request, call_next):
    response=await call_next(request)
    if request.url.path.startswith("/api/v1/") and request.method=="GET": response.headers["Cache-Control"]="no-store"
    return response

def build_plan(request: PlanGenerationRequest, *, internal_planner_choice: PlannerChoice | None = None, server_authorized_planner_choice: PlannerChoice | None = None, prior_plan: dict | None = None, routing_mode: str | None = None, history_actuals: dict[str, dict] | None = None, planning_date: date | None = None) -> dict:
    """Run shared pre-planner work, then the selected in-memory planner.

    ``internal_planner_choice`` and ``server_authorized_planner_choice`` are
    intentionally unavailable to public request schemas. The latter is only
    supplied after the authenticated route has verified athlete ownership and
    selected the server-side default or emergency rollback route.
    """
    input_started = perf_counter()
    profile = normalize_recurring_schedule_for_planning(request.athlete_profile)
    errors = validate_profile(profile)
    if errors:
        raise HTTPException(status_code=422, detail={"error_code":"profile_validation","validation_errors": errors,"diagnostic":{"conflict_type":"profile_validation","reason":errors[0],"validation_rule":"profile_validation"}})
    bands = build_intensity_profile(profile, CONFIG)
    power = build_power_profile(profile, CONFIG)
    if internal_planner_choice is not None and server_authorized_planner_choice is not None:
        raise PlannerRoutingError("ambiguous_planner_choice")
    choice = server_authorized_planner_choice or resolve_planner_choice(internal_planner_choice)
    if choice is PlannerChoice.V2:
        _log_generation_timing(planner="v2", stage="api_input_orchestration", started=input_started)
    fallback_reason_code = None
    fallback_diagnostics: dict = {}
    try:
        plan = execute_selected_planner(choice=choice, profile=profile, bands=bands, power=power, locked_sessions=request.locked_sessions, prior_plan=prior_plan, history_actuals=history_actuals, planning_date=planning_date)
    except PlannerRoutingError as error:
        if choice is not PlannerChoice.V2 or error.reason_code not in _V2_FALLBACK_ELIGIBLE or not v1_fallback_allowed(prior_plan):
            raise
        fallback_reason_code = error.reason_code
        fallback_diagnostics = error.diagnostics
        plan = execute_selected_planner(choice=PlannerChoice.V1, profile=profile, bands=bands, power=power, locked_sessions=request.locked_sessions)
        # Add routing provenance only for an actual V2-to-V1 recovery; pure
        # public V1 output stays byte/behavior compatible.
        routing = {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": error.reason_code}
        if error.diagnostics:
            routing["failure_diagnostics"] = error.diagnostics
        plan = {**plan, "planner_routing": routing}
    except PlanningConflict as error: raise HTTPException(status_code=422, detail={"error_code":"planning_conflict","planning_conflicts":[str(error)],"diagnostic":error.details}) from error
    hard_errors = hard_constraint_errors(plan, profile)
    if hard_errors:
        raise HTTPException(status_code=422, detail={"error_code":"hard_constraint","constraint_errors":hard_errors,"planning_conflicts":hard_errors,"diagnostic":{"conflict_type":"hard_constraint","reason":hard_errors[0],"validation_rule":"hard_constraint_errors"}})
    # V3 is deliberately shadow-only.  It receives the same normalized profile,
    # bands/power anchors, locks, prior plan, completed history, and explicit
    # planning-date boundary as V2.  Public fresh generation has no explicit
    # historical boundary in V2 today, so it is skipped rather than compared
    # against materially different inputs.
    if choice is PlannerChoice.V2 and replacement_planner_shadow_enabled() and planning_date is not None:
        log_replacement_shadow(run_replacement_shadow(
            profile=profile, bands=bands, power=power,
            locked_sessions=request.locked_sessions, v2_plan=plan,
            prior_plan=prior_plan, history_actuals=history_actuals,
            planning_date=planning_date,
        ))
    if fallback_reason_code:
        fallback_mode = f"{routing_mode or 'v2_default'}_fallback"
        log_planner_generation(authoritative_planner="v1", fallback_used=True, fallback_reason_code=fallback_reason_code, failure_diagnostics=fallback_diagnostics, mode=fallback_mode)
    elif choice is PlannerChoice.V1 and v2_planner_shadow_enabled():
        shadow = run_v2_shadow(profile=profile, bands=bands, power=power, locked_sessions=request.locked_sessions, v1_plan=plan)
        log_planner_generation(authoritative_planner="v1", shadow=shadow)
    elif choice is PlannerChoice.V2:
        log_planner_generation(authoritative_planner="v2", mode=routing_mode or "v2_default")
    elif routing_mode:
        log_planner_generation(authoritative_planner="v1", mode=routing_mode)
    return plan

def owned_athlete(athlete_id: str, user_id: str) -> dict:
    profile=REPOSITORIES.get(athlete_id)
    if not profile: raise HTTPException(404,"Athlete not found")
    if REPOSITORIES.athlete_owner(athlete_id) != user_id: raise HTTPException(403,"This athlete belongs to another account.")
    return profile

def safe_generation_metadata(profile: dict) -> dict:
    """Log only structural planner context for an unexpected generation failure."""
    activities=profile.get("recurring_activities")
    tests=profile.get("tests") if isinstance(profile.get("tests"),dict) else {}
    return {
        "recurring_activities_present":activities is not None,
        "recurring_activity_count":len(activities) if isinstance(activities,list) else 0,
        "race_count":len(profile.get("races",[])) if isinstance(profile.get("races"),list) else 0,
        "performance_test_count":len(tests.get("multi_duration_power_tests",[])) if isinstance(tests.get("multi_duration_power_tests"),list) else 0,
        "season_dates_present":bool(profile.get("season",{}).get("start_date") and profile.get("season",{}).get("end_date")),
    }


def safe_planner_failure_diagnostics(error: Exception) -> dict:
    """Keep contract failures actionable without emitting plan/profile payloads."""

    if not isinstance(error, PlannerRoutingError):
        return {}
    source = error.diagnostics if isinstance(error.diagnostics, dict) else {}
    allowed = {
        "reason_code", "failure_stage", "failure_origin", "placement_id", "date",
        "role", "quality_type", "authority_type", "source_date", "expected_date",
        "preservation_status", "changed_fields", "missing_fields",
    }
    result = {key: source[key] for key in sorted(allowed & set(source))}
    identity = source.get("session_identity") or source.get("source_session_identity")
    if isinstance(identity, dict):
        result["session_identity"] = {
            key: identity[key] for key in ("date", "session_id", "mode") if key in identity
        }
    return result

def unexpected_generation_error(error: Exception, profile: dict, athlete_id: str | None, stage: str, planversion_creation_started: bool) -> HTTPException:
    error_id=f"pg_{token_urlsafe(6).replace('-', '').replace('_', '')}"
    trace=extract_tb(error.__traceback__)
    source=f"{trace[-1].filename}:{trace[-1].lineno}" if trace else "unknown"
    metadata=safe_generation_metadata(profile)
    planner_diagnostics=safe_planner_failure_diagnostics(error)
    logger.exception(
        "plan_generation_failed error_id=%s exception_class=%s exception_message=%s source=%s stage=%s athlete_id_present=%s planversion_creation_started=%s recurring_activities_present=%s recurring_activity_count=%s race_count=%s performance_test_count=%s season_dates_present=%s planner_diagnostics=%s",
        error_id, type(error).__name__, str(error), source, stage, bool(athlete_id), planversion_creation_started,
        metadata["recurring_activities_present"], metadata["recurring_activity_count"], metadata["race_count"], metadata["performance_test_count"], metadata["season_dates_present"], planner_diagnostics,
    )
    return HTTPException(500, detail={"error_code":"plan_generation_failed","error_id":error_id})

def generation_rejection_diagnostic(detail: object, error_code: str) -> dict:
    """Keep known 422 reasons visible even when older paths return plain lists or strings."""
    source=detail if isinstance(detail,dict) else {}
    diagnostic=source.get("diagnostic") if isinstance(source.get("diagnostic"),dict) else {}
    result=dict(diagnostic)
    for key in ("planning_conflicts","constraint_errors","validation_errors"):
        values=source.get(key)
        if not isinstance(values,list) or not values:
            continue
        first=values[0]
        if isinstance(first,dict):
            result={**first,**result}
        elif isinstance(first,str) and first:
            result.setdefault("reason",first)
        break
    if isinstance(detail,str) and detail:
        result.setdefault("reason",detail)
    result.setdefault("conflict_type",error_code)
    result.setdefault("reason","request_rejected")
    return result
def athlete_response(athlete_id: str, profile: dict) -> AthleteResponse:
    return AthleteResponse(athlete_id=athlete_id, athlete_profile=public_profile(profile), profile_revision=profile_revision(profile))
def athlete_summary(record: dict) -> dict:
    profile=record["athlete_profile"]
    athlete=profile.get("athlete",{})
    season=profile.get("season",{})
    blocks=profile.get("tests",{}).get("testing_blocks",[])
    deletion_status=REPOSITORIES.deletion_status(record["athlete_id"])
    return {"athlete_id":record["athlete_id"],"created_at":record["created_at"],"updated_at":record["updated_at"],"display_name":athlete.get("display_name") or "Unnamed rower","season_name":season.get("season_name") or "","season_start":season.get("start_date"),"season_end":season.get("end_date"),"race_count":len(profile.get("races",[])),"recurring_activity_count":len(profile.get("recurring_activities",[])),"performance_test_count":sum(len(block.get("performance_tests",[])) for block in blocks if isinstance(block,dict)),"plan_id":record.get("plan_id"),"deletion_status":deletion_status,"can_delete":deletion_status in {"eligible","configured"}}
def owned_plan(plan_id: str, user_id: str) -> dict:
    record=REPOSITORIES.get_plan(plan_id)
    if not record: raise HTTPException(404,"Plan not found")
    if REPOSITORIES.plan_owner(plan_id) != user_id: raise HTTPException(403,"This plan belongs to another account.")
    return record
def plan_needs_update(record: dict, profile: dict) -> bool:
    return record["plan"].get("schedule_signature") != schedule_signature(profile)
def stable_session_key(session: dict) -> str:
    return f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}'
def coached_session(session: dict) -> bool:
    return session.get("session_id")=="COACHED" or session.get("coached") is True
def rowing_session(session: dict) -> bool:
    """Rows, coached or independent, share one actual-load model."""
    return session.get("mode") in {"erg","on_water"} and session.get("session_id") != "RACE"
def actual_by_key(plan_id: str) -> dict[str, dict]:
    record=REPOSITORIES.get_plan(plan_id)
    context=(record or {}).get("plan",{}).get("generation_input_context",{})
    source_ids=[str(item) for item in context.get("actual_plan_ids",()) if item] if isinstance(context,dict) else []
    source_ids.append(plan_id)
    actuals={}
    for source_id in dict.fromkeys(source_ids):
        actuals.update({entry["session_key"]:entry["payload"] for entry in REPOSITORIES.logs_for_plan(source_id)})
    # An accepted adjustment is a new immutable PlanVersion.  Its source log
    # remains canonical on the source version, so inherit it for display
    # rather than cloning a second completion record.
    for provenance in (record or {}).get("plan",{}).get("adjustment_provenance",[]):
        source=provenance.get("source_plan_id")
        if source:
            actuals={**{entry["session_key"]:entry["payload"] for entry in REPOSITORIES.logs_for_plan(source)},**actuals}
    return actuals


def optional_activity_session_key(activity_date: str, activity_type: str) -> str:
    return f"actual:{activity_date}:{activity_type}"


def optional_activity_history_session(session_key: str, payload: dict) -> dict:
    """Adapt an optional actual into the existing completed-session history contract.

    The recorded activity remains a log; this immutable shape is only planner
    history.  Its key is retained in ``source_id`` so repeated regeneration
    cannot manufacture a second completed exposure.
    """
    _, activity_date, activity_type = session_key.split(":", 2)
    mode={"erg":"erg", "on_water":"on_water", "strength":"strength"}.get(activity_type, "other")
    minutes=payload.get("actual_duration_min") or 0
    session={
        "date":activity_date,
        "session_id":f"ACTUAL_{activity_type.upper()}",
        "mode":mode,
        "source_id":session_key,
        "title":f"Completed {activity_type.replace('_', ' ')}",
        "total_cardio_minutes":minutes if mode in {"erg", "on_water"} else 0,
        "strength_minutes":minutes if mode == "strength" else 0,
        "total_training_minutes":minutes,
    }
    if payload.get("actual_intensity"):
        session["band"]=payload["actual_intensity"]
    return session


def optional_actuals_for_day(plan_id: str, activity_date: str) -> list[dict]:
    prefix=f"actual:{activity_date}:"
    return [
        {"session_key":key, **payload}
        for key, payload in actual_by_key(plan_id).items()
        if key.startswith(prefix)
    ]

def session_load_classification(payload: dict) -> str:
    if payload.get("completion")=="no" or payload.get("status")=="skipped": return "missed"
    composition=actual_composition(payload)
    if composition["quality_seconds"] >= 8*60: return "unusually_hard_or_long" if composition["quality_seconds"] >= 25*60 else "quality_hard"
    intensity=payload.get("actual_intensity")
    rpe=payload.get("rpe") or 0
    minutes=payload.get("actual_duration_min") or 0
    if intensity in {"AT","TR","AN","PP"} or (intensity=="mixed_unsure" and rpe>=7): return "unusually_hard_or_long" if minutes>=75 or rpe>=9 else "quality_hard"
    if intensity in {"UT2","UT1"} or (intensity=="mixed_unsure" and rpe>=5): return "aerobic_moderate"
    return "easy_technical"
def actual_composition(payload: dict) -> dict:
    """Describe athlete-entered segments without inferring unknown portions."""
    totals={"easy_technical_seconds":0,"aerobic_seconds":0,"quality_seconds":0,"unclassified_seconds":0}
    for segment in payload.get("actual_segments",[]):
        seconds=(segment.get("duration_seconds") or 0)*max(1,segment.get("repetitions") or 1)
        band=segment.get("intensity_band")
        if band in {"AT","TR","AN","PP"}: bucket="quality_seconds"
        elif band in {"UT2","UT1"}: bucket="aerobic_seconds"
        elif band=="UT3" or segment.get("segment_type") in {"warm_up","technical_drill","easy_rowing","recovery","cooldown_return"}: bucket="easy_technical_seconds"
        else: bucket="unclassified_seconds"
        totals[bucket]+=seconds
    known=sum(totals.values())
    if payload.get("actual_duration_min") and known < payload["actual_duration_min"]*60: totals["unclassified_seconds"]+=payload["actual_duration_min"]*60-known
    return {**totals,"total_seconds":sum(totals.values())}
def impact_explanation(record: dict, source: dict, classification: str, changes: list[dict], payload: dict) -> str:
    composition=actual_composition(payload); quality=composition["quality_seconds"]//60
    after=sorted((s for s in record["plan"].get("sessions",[]) if s["date"]>source["date"]),key=lambda s:s["date"])
    next_row=next((s for s in after if s.get("mode") in {"erg","on_water"}),None)
    recovery=[s.get("title","session").lower() for s in after if next_row and s["date"]<next_row["date"]]
    detail=f"This session included about {quality} minutes of high-intensity rowing." if quality else "The logged load fits the planned coaching session."
    weekly=weekly_quality_context(record,source,payload)
    if changes and weekly["completed_exposures"]>=2: return f"Review recommended. You have already completed {weekly['completed_exposures']} quality rowing exposures in the recent dated recovery context: {', '.join(weekly['labels'])}. A lower-intensity stimulus is suggested instead."
    if changes: return f"{detail} Your next rowing session is quality work, so its spacing should be reviewed."
    if next_row: return f"{detail} The next rowing session is {next_row.get('band','planned work')}; {' and '.join(recovery) or 'the available spacing'} supports keeping the current plan."
    return f"{detail} No later rowing session this week needs changing."
def weekly_quality_context(record: dict, source: dict, source_payload: dict) -> dict:
    """Count completed quality exposures in the recovery lookback, not a display week."""
    source_date=date.fromisoformat(source["date"]); lookback=source_date-timedelta(days=7)
    actuals=actual_by_key(record["plan_id"]); actuals[stable_session_key(source)]=source_payload
    labels=[]; minutes=0
    for session in record["plan"].get("sessions",[]):
        session_date=date.fromisoformat(session["date"])
        if not lookback<=session_date<=source_date: continue
        actual=actuals.get(stable_session_key(session))
        if not actual or actual.get("completion")=="no" or actual.get("status")=="skipped": continue
        composition=actual_composition(actual)
        quality_seconds=composition["quality_seconds"]
        is_quality=quality_seconds>=8*60 or (not actual.get("actual_segments") and any(b in str(actual.get("actual_intensity") or session.get("band","")) for b in ("AT","TR","AN","PP")))
        if is_quality:
            labels.append(session.get("title") or session.get("session_role") or "quality work")
            minutes+=quality_seconds//60
    return {"completed_exposures":len(labels),"quality_minutes":minutes,"labels":labels}
def bounded_coached_proposal(record: dict, session_key: str, payload: dict) -> dict:
    source=next((s for s in record["plan"].get("sessions",[]) if stable_session_key(s)==session_key),None)
    if not source or not rowing_session(source): raise HTTPException(422,"Only rowing sessions can use segmented actual logging.")
    classification=session_load_classification(payload)
    if classification not in {"quality_hard","unusually_hard_or_long"}: return {"recommendation":"none","classification":classification,"changes":[],"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,[],payload)}
    source_date=date.fromisoformat(source["date"])
    # Recovery spacing is based on elapsed dates.  A hard Sunday must be
    # visible to a planned hard Monday even though the UI groups them apart.
    candidates=sorted((s for s in record["plan"].get("sessions",[]) if 0 < (date.fromisoformat(s["date"])-source_date).days <= 7 and any(b in str(s.get("band","")) for b in ("AT","TR","AN","PP"))),key=lambda s:s["date"])
    if not candidates: return {"recommendation":"none","classification":classification,"changes":[],"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,[],payload)}
    current=candidates[0]; source_label="coached" if coached_session(source) else "logged"
    suggested={**current,"band":"UT2","title":"Long aerobic","structure":"60 min UT2","description":f"Adjusted after an unexpectedly hard {source_label} rowing session.","adjustment_reason":"rowing_session_actual"}
    changes=[{"session_key":stable_session_key(current),"date":current["date"],"current":current,"suggested":suggested,"why":"The logged rowing load added an unexpected hard exposure; this preserves recovery spacing."}]
    return {"recommendation":"review","classification":classification,"source_session_id":session_key,"changes":changes,"composition":actual_composition(payload),"explanation":impact_explanation(record,source,classification,changes,payload)}
def enrich_sessions(plan_id: str, sessions: list[dict]) -> list[dict]:
    actuals=actual_by_key(plan_id); enriched=[]; cues=[]
    for session in sessions:
        item=deepcopy(session); actual=actuals.get(stable_session_key(session))
        if actual: item["actual"]=actual
        if actual and actual.get("carry_cue_forward") and (actual.get("coach_cues") or actual.get("technical_note")):
            cues.append(actual.get("coach_cues") or actual.get("technical_note"))
        elif cues and session.get("mode") in {"on_water","erg"}: item["technical_cues"]=cues[-1:]
        enriched.append(item)
    return enriched
def require_coach_admin(user_id: str = Depends(current_user_id)) -> str:
    allowed={value.strip() for value in os.getenv("COACH_ADMIN_USER_IDS","").split(",") if value.strip()}
    if user_id not in allowed: raise HTTPException(403,"Coach/admin access is required for race postings.")
    return user_id

@app.get("/api/v1/health", response_model=ApiHealth)
def health() -> ApiHealth:
    return ApiHealth(
        status="ok",
        api_version="v1",
        planner_version=PLANNER_VERSION,
        health_schema="2",
        build_id=os.getenv("VERCEL_GIT_COMMIT_SHA", "unknown")[:12] or "unknown",
        deployment_url=os.getenv("VERCEL_URL", "unknown") or "unknown",
    )

@app.get("/api/v1/ready")
def ready() -> dict:
    if os.getenv("VERCEL") != "1": return {"status":"ok","environment":"local"}
    required=["SUPABASE_DB_URL","SUPABASE_URL","SUPABASE_PUBLISHABLE_KEY","ALLOWED_ORIGINS"]
    missing=[name for name in required if not os.getenv(name)]
    if os.getenv("REQUIRE_AUTH", "false").lower() != "true": missing.append("REQUIRE_AUTH=true")
    if missing: raise HTTPException(503, {"status":"not_ready","missing":missing})
    return {"status":"ok","environment":"vercel"}

@app.post("/api/v1/plans/generate", response_model=PlanResponse)
def generate(request: PlanGenerationRequest) -> PlanResponse:
    choice=default_planner_choice()
    plan = build_plan(request, server_authorized_planner_choice=choice, routing_mode="v1_emergency_rollback" if choice is PlannerChoice.V1 else "v2_default")
    athlete_id=REPOSITORIES.create(request.athlete_profile)
    return PlanResponse(plan_id=REPOSITORIES.save_plan(athlete_id, plan), plan=plan)

@app.post("/api/v1/athletes", response_model=AthleteResponse)
def create_athlete(request: AthleteCreateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    profile=with_profile_revision(request.athlete_profile, 0)
    athlete_id=REPOSITORIES.create(profile, user_id)
    return athlete_response(athlete_id, profile)

@app.post("/api/v1/account/onboarding-athlete", response_model=AthleteResponse)
def get_or_create_onboarding_athlete(request: AthleteCreateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    """The only creation path used by first-run onboarding; safe under retries."""
    errors=validate_profile(request.athlete_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    profile=with_profile_revision(request.athlete_profile, 0)
    athlete_id, stored_profile, _=REPOSITORIES.get_or_create_for_user(profile, user_id)
    return athlete_response(athlete_id, stored_profile)

@app.get("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def get_athlete(athlete_id: str, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    profile=owned_athlete(athlete_id,user_id)
    return athlete_response(athlete_id, profile)

@app.get("/api/v1/account/athlete")
def get_current_athlete(user_id: str = Depends(current_user_id)) -> dict:
    record=REPOSITORIES.latest_for_user(user_id)
    if not record: raise HTTPException(404,"No Athlete Profile is associated with this account.")
    return {"athlete_id":record["athlete_id"],"athlete_profile":public_profile(record["athlete_profile"]),"profile_revision":profile_revision(record["athlete_profile"]),"plan_id":record.get("plan_id")}

@app.get("/api/v1/account/athletes")
def get_account_athletes(user_id: str = Depends(current_user_id)) -> dict:
    return {"athletes":[athlete_summary(record) for record in REPOSITORIES.list_for_user(user_id)]}

@app.delete("/api/v1/account/athletes/{athlete_id}")
def delete_account_athlete(athlete_id: str, selected_athlete_id: Optional[str] = None, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id, user_id)
    if selected_athlete_id == athlete_id:
        raise HTTPException(409, "Select another athlete profile before deleting this one.")
    status=REPOSITORIES.delete_empty_athlete(athlete_id)
    if status == "deleted": return {"status":"deleted","athlete_id":athlete_id}
    if status == "not_found": raise HTTPException(404,"Athlete not found")
    reasons={"plan_versions":"Profiles with generated plans cannot be deleted.","athlete_records":"Profiles with saved training history cannot be deleted."}
    raise HTTPException(409, reasons.get(status,"This athlete profile cannot be deleted."))

@app.put("/api/v1/athletes/{athlete_id}", response_model=AthleteResponse)
def update_athlete(athlete_id: str, request: AthleteUpdateRequest, user_id: str = Depends(current_user_id)) -> AthleteResponse:
    current=owned_athlete(athlete_id,user_id)
    updated_profile=normalize_recurring_schedule_for_planning(request.athlete_profile)
    errors=validate_profile(updated_profile)
    if errors: raise HTTPException(status_code=422, detail={"validation_errors":errors})
    current_revision=profile_revision(current)
    if request.expected_revision != current_revision: raise HTTPException(status_code=409, detail={"message":"This profile was updated in another tab or session.","current_revision":current_revision})
    profile=with_profile_revision(updated_profile, current_revision+1)
    if not REPOSITORIES.save_if_revision(athlete_id, profile, current_revision):
        latest=REPOSITORIES.get(athlete_id) or current
        raise HTTPException(status_code=409, detail={"message":"This profile was updated in another tab or session.","current_revision":profile_revision(latest)})
    return athlete_response(athlete_id, profile)

@app.post("/api/v1/athletes/{athlete_id}/plans/generate", response_model=PlanResponse)
def generate_for_athlete(athlete_id: str, request: RegenerateRequest, user_id: str = Depends(current_user_id)) -> PlanResponse:
    profile=owned_athlete(athlete_id,user_id)
    stage="locked_session_loading"; planversion_creation_started=False
    choice=default_planner_choice()
    try:
        previous=REPOSITORIES.latest_plan_for_athlete(athlete_id)
        locked=list(request.locked_sessions)
        inherited_locked, effective_actuals, actual_plan_ids = _generation_history(previous)
        locked.extend(inherited_locked)
        choice=resolve_authenticated_athlete_planner()
        prior_plan=previous["plan"] if previous else None
        fingerprint=generation_input_fingerprint(
            profile=profile,
            choice=choice,
            locked_sessions=locked,
            actuals=effective_actuals,
            weekly_overrides=REPOSITORIES.weekly_overrides(athlete_id),
            prior_plan=prior_plan,
        )
        if plan_matches_generation_inputs(previous, fingerprint):
            authoritative=fingerprint["planner"]
            log_planner_generation(authoritative_planner=authoritative, mode="noop_existing_plan", regeneration_skipped=True)
            return PlanResponse(plan_id=previous["plan_id"], plan=previous["plan"])
        if choice is PlannerChoice.V1 and not v1_fallback_allowed(prior_plan):
            logger.warning("planner_generation mode=v1_emergency_rollback_blocked future_authority_blocks_v1_transition=true")
            raise HTTPException(status_code=409, detail={"error_code":"v2_future_authority_requires_resolution", "diagnostic":{"conflict_type":"planner_transition", "reason":"v2_future_authority_requires_resolution"}})
        stage="plan_generation"
        plan=build_plan(
            PlanGenerationRequest(athlete_profile=profile, locked_sessions=locked),
            server_authorized_planner_choice=choice,
            prior_plan=prior_plan,
            routing_mode="v2_default" if choice is PlannerChoice.V2 else "v1_emergency_rollback",
            history_actuals=effective_actuals,
            planning_date=current_planning_date(),
        )
        actual_choice=_authoritative_choice_for_plan(plan, choice)
        plan["generation_input_context"]={
            "locked_sessions": tuple(sorted((dict(item) for item in locked), key=_canonical_json)),
            "actual_plan_ids": actual_plan_ids,
        }
        plan["generation_input_fingerprint"]=generation_input_fingerprint(
            profile=profile,
            choice=actual_choice,
            locked_sessions=locked,
            actuals=effective_actuals,
            weekly_overrides=REPOSITORIES.weekly_overrides(athlete_id),
            prior_plan=prior_plan,
        )
    except HTTPException as error:
        detail=error.detail if isinstance(error.detail, dict) else {}
        code=detail.get("error_code", "planning_rejected" if error.status_code == 422 else "request_rejected")
        diagnostic=generation_rejection_diagnostic(error.detail, code)
        logger.warning("plan_generation_failed endpoint=athlete_regenerate status=%s code=%s conflict_type=%s reason=%s activity_type=%s scheduling_status=%s requested_frequency=%s candidate_days=%s prohibited_days=%s fixed_days=%s week_start=%s validation_rule=%s", error.status_code, code, diagnostic.get("conflict_type","request_rejected"), diagnostic.get("reason","request_rejected"), diagnostic.get("activity_type"), diagnostic.get("scheduling_status"), diagnostic.get("requested_frequency"), diagnostic.get("candidate_days"), diagnostic.get("prohibited_days"), diagnostic.get("fixed_days"), diagnostic.get("week_start"), diagnostic.get("validation_rule"))
        raise
    except PlannerRoutingError as error:
        if choice is PlannerChoice.V2 and error.reason_code in _V2_FALLBACK_ELIGIBLE and not v1_fallback_allowed(previous["plan"] if previous else None):
            logger.warning("planner_generation mode=v2_default_fallback_blocked future_authority_blocks_v1_transition=true")
            raise HTTPException(status_code=409, detail={"error_code":"v2_fallback_blocked_by_future_authority", "diagnostic":{"conflict_type":"planner_transition", "reason":"v2_fallback_blocked_by_future_authority"}}) from error
        raise unexpected_generation_error(error, profile, athlete_id, stage, planversion_creation_started) from error
    except Exception as error:
        raise unexpected_generation_error(error, profile, athlete_id, stage, planversion_creation_started) from error
    try:
        stage="persistence"; planversion_creation_started=True
        persistence_started=perf_counter()
        plan_id=REPOSITORIES.save_plan(athlete_id,plan)
        if _authoritative_choice_for_plan(plan, choice) is PlannerChoice.V2:
            _log_generation_timing(planner="v2", stage="persistence", started=persistence_started)
    except Exception as error:
        raise unexpected_generation_error(error, profile, athlete_id, stage, planversion_creation_started) from error
    return PlanResponse(plan_id=plan_id, plan=plan)

@app.get("/api/v1/athletes/{athlete_id}/plans/latest")
def latest_plan_for_athlete(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    record=REPOSITORIES.latest_plan_for_athlete(athlete_id)
    if not record: raise HTTPException(404,"No PlanVersion exists for this athlete.")
    profile=REPOSITORIES.get(athlete_id) or {}
    return {**record,"plan_needs_update":plan_needs_update(record,profile)}

@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    return {**record,"plan_needs_update":plan_needs_update(record,profile)}

@app.get("/api/v1/plans/{plan_id}/today")
def today(plan_id: str, on: Optional[date] = None, user_id: str = Depends(current_user_id)) -> dict:
    record = owned_plan(plan_id,user_id)
    target = (on or date.today()).isoformat()
    sessions = [s for s in record["plan"]["sessions"] if s["date"] == target]
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    calendar={item["date"]:item for item in record["plan"].get("calendar_days",[])}
    return {"plan_id": plan_id, "plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile),"date": target, "state":calendar.get(target,{}).get("state","no_additional_session"), "sessions": enrich_sessions(plan_id,sessions), "actual_activities":optional_actuals_for_day(plan_id,target), "cached_at": date.today().isoformat()}

@app.get("/api/v1/plans/{plan_id}/week")
def week(plan_id: str, week_number: Optional[int] = None, week_start: Optional[date] = None, user_id: str = Depends(current_user_id)) -> dict:
    record = owned_plan(plan_id,user_id)
    sessions = record["plan"]["sessions"]
    if week_start:
        monday=week_start-timedelta(days=week_start.weekday())
        sessions=[s for s in sessions if monday <= date.fromisoformat(s["date"]) < monday+timedelta(days=7)]
    else:
        if week_number is None: raise HTTPException(422,"Provide week_start or week_number")
        sessions=[s for s in sessions if date.fromisoformat(s["date"]).isocalendar().week == week_number]
        if not sessions: return {"plan_id": plan_id, "week": week_number, "days": []}
        first=min(date.fromisoformat(s["date"]) for s in sessions)
        monday=first-timedelta(days=first.weekday())
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    calendar={item["date"]:item for item in record["plan"].get("calendar_days",[])}
    # The newest saved override for this week takes precedence.  It is applied
    # only to this read model; the underlying plan and permanent profile stay intact.
    matching_override=next((item["payload"] for item in REPOSITORIES.weekly_overrides(record["athlete_id"])
                            if item["payload"].get("week_start")==monday.isoformat()), None)
    if matching_override:
        from rowing_plan.weekly_overrides import apply_to_sessions
        sessions=apply_to_sessions(sessions,matching_override)
    enriched_sessions=enrich_sessions(plan_id,sessions)
    days=[]
    for offset in range(7):
        current=monday+timedelta(days=offset); day_sessions=[s for s in enriched_sessions if s["date"]==current.isoformat()]
        state=calendar.get(current.isoformat(),{}).get("state","no_additional_session")
        days.append({"date":current.isoformat(),"day":current.strftime("%A"),"state":state,"sessions":day_sessions,"actual_activities":optional_actuals_for_day(plan_id,current.isoformat())})
    return {"plan_id": plan_id,"plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile), "week": monday.isocalendar().week, "week_start":monday.isoformat(), "days": days, "weekly_override_applied":bool(matching_override)}

@app.get("/api/v1/plans/{plan_id}/calendar")
def calendar(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id); profile=REPOSITORIES.get(record["athlete_id"]) or {}
    sessions_by_date={}
    for session in record["plan"].get("sessions",[]): sessions_by_date.setdefault(session["date"],[]).append(session)
    days=[{**item,"sessions":sessions_by_date.get(item["date"],[])} for item in record["plan"].get("calendar_days",[])]
    return {"plan_id":plan_id,"plan_version":record["version_number"],"plan_needs_update":plan_needs_update(record,profile),"days":days,"phases":record["plan"].get("phases",[])}

@app.get("/api/v1/plans/{plan_id}/sessions/detail")
def session_detail(plan_id: str, session_date: date, session_id: str, mode: str, user_id: str = Depends(current_user_id)) -> dict:
    """Mobile display adapter; planning calculations remain in the engine."""
    record=owned_plan(plan_id,user_id)
    session=next((s for s in record["plan"]["sessions"] if s["date"]==session_date.isoformat() and s["session_id"]==session_id and s["mode"]==mode),None)
    if not session: raise HTTPException(404, "Session not found")
    type_map={"on_water":"row_water","erg":"row_erg","strength":"strength","race":"race","treadmill_walk_jog":"alternate_ut2","elliptical":"alternate_ut2","bike":"cross_training"}
    session_type="coached_lesson" if session_id=="COACHED" else type_map.get(mode,"cross_training")
    return {"session_id":session_id,"date":session["date"],"session_type":session_type,"title":session["title"],"primary_band":session.get("band"),"planned_duration_min":session.get("total_cardio_minutes",0),"segments":[{"type":"main","duration_min":session.get("total_cardio_minutes",0),"description":session.get("structure","")}],"erg_targets":{"watts":session.get("target_watts"),"split":session.get("split_guide"),"rate":session.get("rating"),"hr":session.get("hr_range")} if mode=="erg" else None,"water_targets":{"rate":session.get("rating"),"hr":session.get("hr_range"),"technical_cue":session.get("technical_cue"),"note":"Water speed varies with current, wind, steering, boat class, and direction."} if mode=="on_water" else None,"coach_directed":session_id=="COACHED","description":session.get("description",session.get("structure","")),"recovery":session.get("recovery"),"rpe_guidance":session.get("rating")}

@app.post("/api/v1/plans/{plan_id}/sessions/{session_key}/log")
def log_workout(plan_id: str, session_key: str, log: WorkoutLogRequest, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    payload=log.model_dump()
    source=next((s for s in record["plan"].get("sessions",[]) if stable_session_key(s)==session_key),None)
    if not source: raise HTTPException(404,"Session not found")
    if not coached_session(source):
        payload["coach_cues"]=""
        payload["carry_cue_forward"]=False
    proposal=bounded_coached_proposal(record,session_key,payload) if rowing_session(source) else {"recommendation":"none","classification":"logged","changes":[]}
    return {"status": "accepted", "log_id": REPOSITORIES.save_log(plan_id, session_key, payload), "session_load_au":session_load_au(payload), "adjustment":proposal}


@app.post("/api/v1/plans/{plan_id}/days/{activity_date}/actuals")
def log_optional_activity(plan_id: str, activity_date: date, log: OptionalActivityLogRequest, user_id: str = Depends(current_user_id)) -> dict:
    """Record an athlete activity on a prescribed Off/rest day without changing it."""
    record=owned_plan(plan_id,user_id)
    activity_day=activity_date.isoformat()
    calendar={item["date"]:item for item in record["plan"].get("calendar_days",[])}
    if calendar.get(activity_day,{}).get("state") != "designated_rest":
        raise HTTPException(422,"Optional activities may be recorded only on a planned Off/rest day.")
    if any(session.get("date")==activity_day for session in record["plan"].get("sessions",[])):
        raise HTTPException(422,"Optional activities may be recorded only when no session is planned.")
    payload=log.model_dump()
    payload["completed_date"]=activity_day
    payload["source"]="optional_off_day_actual"
    session_key=optional_activity_session_key(activity_day,payload["activity_type"])
    return {"status":"accepted", "log_id":REPOSITORIES.save_log(plan_id,session_key,payload), "session_key":session_key}

@app.post("/api/v1/plans/{plan_id}/coached-session-adjustment/apply")
def apply_coached_session_adjustment(plan_id: str, session_key: str, user_id: str = Depends(current_user_id)) -> dict:
    """Create a new version only after the athlete accepts the bounded proposal."""
    record=owned_plan(plan_id,user_id); actual=actual_by_key(plan_id).get(session_key)
    if not actual: raise HTTPException(422,"Log the coached session before reviewing the remaining week.")
    proposal=bounded_coached_proposal(record,session_key,actual)
    if proposal["recommendation"]!="review": return {"status":"no_change","plan_id":plan_id,"proposal":proposal}
    plan=deepcopy(record["plan"]); changes={item["session_key"]:item["suggested"] for item in proposal["changes"]}
    plan["sessions"]=[changes.get(stable_session_key(session),session) for session in plan.get("sessions",[])]
    plan.setdefault("adjustment_provenance",[]).append({"reason":"rowing_session_actual","source_session_id":session_key,"source_plan_id":plan_id,"changes":[item["session_key"] for item in proposal["changes"]]})
    new_plan_id=REPOSITORIES.save_plan(record["athlete_id"],plan)
    return {"status":"applied","plan_id":new_plan_id,"proposal":proposal}

@app.get("/api/v1/plans/{plan_id}/logs")
def workout_logs(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_plan(plan_id,user_id)
    logs=REPOSITORIES.logs_for_plan(plan_id)
    return {"plan_id":plan_id,"logs":logs,"load_summary":load_summary(logs)}

@app.post("/api/v1/athletes/{athlete_id}/private-check-ins")
def private_check_in(athlete_id: str, entry: PrivateCheckInRequest, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    entry_id=REPOSITORIES.save_private_check_in(athlete_id,entry.model_dump())
    return {"status":"accepted","entry_id":entry_id,"message":"Private tracking is opt-in and never changes training automatically."}

@app.get("/api/v1/athletes/{athlete_id}/private-check-ins")
def private_check_ins(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    entries=REPOSITORIES.private_check_ins(athlete_id)
    high=sum(e["payload"].get("symptom_impact")=="high" for e in entries)
    return {"entries":entries,"suggestion":"Review recovery and session placement with your coach if this reflects a repeated personal pattern." if high>=3 else None,"automatic_plan_change":False}

@app.post("/api/v1/athletes/{athlete_id}/weekly-overrides")
def save_weekly_override(athlete_id: str, override: WeeklyOverrideRequest, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    from rowing_plan.weekly_overrides import normalize
    payload=normalize(override.model_dump())
    return {"override_id":REPOSITORIES.save_weekly_override(athlete_id,payload),"scope":payload["scope"]}

@app.get("/api/v1/athletes/{athlete_id}/weekly-overrides")
def weekly_overrides(athlete_id: str, user_id: str = Depends(current_user_id)) -> dict:
    owned_athlete(athlete_id,user_id)
    return {"overrides":REPOSITORIES.weekly_overrides(athlete_id)}

@app.get("/api/v1/race-postings")
def race_postings(user_id: str = Depends(current_user_id)) -> dict:
    account=REPOSITORIES.latest_for_user(user_id); level=(account or {}).get("athlete_profile",{}).get("athlete",{}).get("experience_level","intermediate")
    items=REPOSITORIES.race_postings()
    return {"postings":[{**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]} for item in items if level in item["payload"].get("audience_levels",[])]}

@app.get("/api/v1/admin/race-postings")
def admin_race_postings(user_id: str = Depends(require_coach_admin)) -> dict:
    return {"postings":[{**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]} for item in REPOSITORIES.race_postings()]}

@app.post("/api/v1/admin/race-postings")
def create_race_posting(posting: RacePostingRequest, user_id: str = Depends(require_coach_admin)) -> dict:
    if posting.end_date < posting.start_date: raise HTTPException(422,"Race posting end date must be on or after start date.")
    if not posting.audience_levels: raise HTTPException(422,"Choose at least one rower group.")
    item=REPOSITORIES.create_race_posting(user_id,posting.model_dump())
    return {**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]}

@app.put("/api/v1/admin/race-postings/{posting_id}")
def update_race_posting(posting_id: str, posting: RacePostingRequest, user_id: str = Depends(require_coach_admin)) -> dict:
    if posting.end_date < posting.start_date: raise HTTPException(422,"Race posting end date must be on or after start date.")
    item=REPOSITORIES.update_race_posting(posting_id,posting.model_dump())
    if not item: raise HTTPException(404,"Race posting not found")
    return {**item["payload"],"posting_id":item["posting_id"],"updated_at":item["updated_at"]}

@app.get("/api/v1/plans/{plan_id}/season")
def season_summary(plan_id: str, user_id: str = Depends(current_user_id)) -> dict:
    record=owned_plan(plan_id,user_id)
    profile=REPOSITORIES.get(record["athlete_id"]) or {}
    today_value=date.today()
    phases=record["plan"].get("phases",[])
    current=next((item for item in phases if item["date"]==today_value.isoformat()), next((item for item in phases if item["date"]>=today_value.isoformat()), None))
    races=profile.get("races",[])
    next_race=next((race for race in sorted(races,key=lambda r:r["start_date"]) if date.fromisoformat(race["start_date"])>=today_value),None)
    transitions=[]; prior=None
    for item in phases:
        if item["phase"]!=prior:
            transitions.append(item); prior=item["phase"]
    return {"current_phase":current,"next_race":next_race,"days_to_next_race":(date.fromisoformat(next_race["start_date"])-today_value).days if next_race else None,"transitions":transitions}

@app.get("/api/v1/plans/{plan_id}/excel")
def excel(plan_id: str, user_id: str = Depends(current_user_id)) -> StreamingResponse:
    record = owned_plan(plan_id, user_id)
    profile=REPOSITORIES.get(record["athlete_id"])
    if not profile: raise HTTPException(404, "Athlete not found")
    buffer=BytesIO(build_workbook(profile, record["plan"]))
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=rowing-plan.xlsx"})
