# Training engine audit (v0.7, phase 0)

Date: 2026-09-02

This audit traces the current deterministic plan-generation path before the
v0.7 periodization work. It does not change the planning engine. The findings
explain why an otherwise valid schedule can look like a calendar-slot filler.

## Current generation path

```text
Athlete Profile
  -> API validation and intensity / power-profile builders
  -> scheduler.generate_plan()
  -> recurring-activity placement for each calendar week
  -> day-by-day phase lookup and availability/commitment handling
  -> fixed daily band choice
  -> session_selector.select_session()
  -> PlanVersion persistence and calendar/read models
```

### Detailed trace for a scheduled row

1. `services/api/app/main.py:build_plan` validates the profile, builds HR/RPE
   bands and the Multi-Duration Rowing Power Profile, then calls
   `rowing_plan.scheduler.generate_plan`.
2. `generate_plan` calls `_recurring_commitments` first. This is the correct
   constraint-first path: fixed, preferred, and flexible commitments are
   placed before ordinary rowing sessions. `schedule_scoring.choose` protects
   approved days and avoids placement overlap.
3. The scheduler then loops one calendar date at a time. It gets the phase
   with `periodization.phase_for_day`, handles race, rest, strength, coached,
   and unavailable days, and otherwise creates a rowing session.
4. `_session` derives mode and a capped duration from weekday availability and
   calls `session_selector.select_session` with a band, phase, race type, and
   the athlete's structure preference. It adds power anchors only for an erg
   session when appropriate.
5. The plan is saved as a new PlanVersion. Profile changes invalidate a plan
   through its schedule signature; regeneration preserves completed sessions.

## Answers to the requested audit questions

### 1. How is a phase currently determined?

`rowing_plan/periodization.py` supplies a daily label. Race priority maps to
fixed taper/recovery lengths (`A: 10/3`, `B: 5/2`, `C: 2/1` days). A day in
that window becomes `taper_sharpen`, `race`, or `race_recovery`; otherwise it
is `race_build` when the next race is fewer than 28 days away and
`specific_preparation` for every other day. The result is a per-day list,
not a phase-plan object with objectives, volume direction, or specificity.

### 2. Does phase currently change workout selection?

Only in a narrow way. On ordinary days, the scheduler chooses `TR` on Tuesday
during `race_build`/`specific_preparation`, `UT3` during
`taper_sharpen`/`race_recovery`, and `UT2` otherwise (with Thursday forced to
UT2). The phase is also passed to the selector as a template tag. It does not
build phase objectives, weekly priorities, or a phase-dependent session mix.

### 3. How is the training band selected for each rowing day?

It is hard-coded in `scheduler.generate_plan` after commitments have been
handled. It is not selected from a weekly training objective, prior workload,
experience pathway, or session history. Coached sessions are fixed as
`UT2/UT1`; alternate post-lifting work is fixed as `UT2`.

### 4. How is a workout structure selected?

`session_selector.select_session` filters `data/session_library.json` by
band, phase tag, race-type tag, supported mode, and total-minute range. It
sorts remaining candidates by a simple preference rank and `session_id`, then
returns the first match. The library has several viable structures, but no
history-aware rotation, fingerprinting, progression, or deliberate repeat
reason.

### 5. Why can the exact same session repeat?

For equal inputs, `select_session` is deterministic and has no date, week,
history, or prior-session input. Since the daily band is usually UT2 and the
duration is usually the same availability cap, the first compatible UT2
template wins on every matching day. Exact repetition is neither detected nor
annotated as benchmark/progression/coaching intent.

### 6. Where are research-source rules actually used at runtime?

`rowing_plan/evidence.py` currently attaches four evidence metadata records to
the generated plan. `rowing_plan/power_profile.py` uses the relevant test
rules to produce session power anchors and plan-impact notes. The scheduling
and template paths use `source_basis_ids` as provenance, but the evidence
records do not currently drive variety, weekly intensity distribution,
progression, taper volume, beginner development, or phase objectives.

### 7. What creates “No Session”?

The API’s calendar model records `designated_rest`, `unavailable`, or
`no_additional_session`. The latter is the default for every date that is not
explicitly rest/unavailable, even if no session is subsequently constructed.
The Month view turns a day with no session into its generic summary, so it can
appear as an unexplained absence. There is no coverage validator tying every
calendar-day state to an intentional plan state after scheduling.

### 8. What currently happens to sessions during taper?

No existing normal-week workout is transformed. The band is changed to UT3,
which removes the normal quality stimulus, while duration remains derived from
availability. The documented/configured taper-volume range is not used by the
scheduler. Frequency can also fall when a date is a race, rest, or commitment;
there is no explicit retain-frequency rule, retained race-pace exposure, or
taper explanation.

### 9. Does `workout_structure_preference` behave as a preference or hard
filter?

It is a tie-breaker, not a hard filter, which is the right intent. However,
the ranking is text-based (`"seconds"`/`"strokes"` for short and
`"minutes"` for long) and runs before history/progression because those
systems do not exist. Consequently it can exert an outsized and repetitive
effect when the candidate set is small.

### 10. Does experience level affect training progression?

No. `athlete.experience_level` is present in profiles but the scheduler and
periodization modules do not read it. There is no new/novice/developing/
experienced/competitive pathway, continuous-row tolerance, or gradual
introduction of moderate/high-intensity work.

## Runtime gaps against v0.7

| Required capability | Current state | Consequence |
| --- | --- | --- |
| Phase plan before sessions | Daily labels only | Phase labels do not reliably change the training model. |
| WeeklyTrainingIntent | Absent | No weekly target, role mix, or load direction. |
| Session roles/fingerprints | Absent | Exact repetition is common and not explainable. |
| Progression | Absent | Duration, repetitions, recovery, rate, and specificity do not evolve deliberately. |
| Taper transformation | Absent | Taper changes a band rather than reducing volume while retaining selected intensity. |
| Coverage validator | Absent | `no_additional_session` can mask an unexplained planned-day gap. |
| Experience pathways | Absent | Novices can receive the same pathway as experienced rowers. |
| Evidence-to-rule metadata | Partial | Existing provenance lacks the requested uniform machine-readable fields and runtime wiring. |

## Components to preserve

The v0.7 work can extend the planning layer without a PWA, authentication,
database, or scheduling-architecture rewrite. These components should remain
the integration boundaries:

- `services/api/app/main.py` plan validation, ownership checks, no-store
  behavior, and PlanVersion endpoints.
- `services/api/app/repositories.py` versioned storage and completed-session
  preservation path.
- `rowing_plan/recurring_activities.py` and `schedule_scoring.py` for the
  fixed/preferred/flexible commitment source of truth.
- Profile schemas and current profile persistence, with additive experience
  fields only where needed.
- `rowing_plan/intensity.py` and `power_profile.py`; they remain the
  personalized target providers rather than becoming a predicted-2k system.
- Existing session library ownership/provenance, expanded with original
  archetypes rather than copied external workouts.
- Current API calendar/today/week models, extended to expose intentional
  state and phase/weekly-intent metadata.
- PlanVersion invalidation and locked/completed-session behavior.

## Recommended implementation boundary for phase 1

Introduce additive planning data models between phase calculation and
`generate_plan` session construction:

```text
phase plan -> weekly intent -> session-role placement -> archetype selection
  -> progression/variety check -> taper transformation -> coverage validation
```

This preserves recurring-placement constraints and persistence while replacing
only the daily hard-coded band/first-template path. The next step should add
the phase and weekly-intent models plus focused tests before expanding the
archetype library.
