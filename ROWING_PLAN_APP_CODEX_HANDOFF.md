# Rowing Plan Generator — Consolidated Codex Handoff v0.2

---

# README

# Rowing Plan Generator — Codex Handoff v0.2

This package specifies a deterministic web app that collects an athlete profile, creates a rowing season around races and fixed weekly commitments, previews the reasoning, and exports a formatted Excel workbook.

## What changed in v0.2

The short-, one-minute-, and sustained rate-capped power tests are now **active planning inputs** through an original feature called the **Multi-Duration Rowing Power Profile**.

The app may use these athlete-owned measurements to:

- personalize PP and AN workout power anchors;
- check whether prescribed power is plausible;
- select goal-relevant sessions;
- describe the athlete's measured power profile without copying a third-party description;
- compare repeated tests longitudinally and modify future training emphasis;
- support an optional coach- or research-configured ratio classifier.

The app must **not** reproduce a third-party spreadsheet, instructions, branding, exact weighted predicted-2k formula, or implied endorsement. It must not automatically claim that a single battery diagnoses LT1, LT2, aerobic capacity, anaerobic capacity, or a physiological weakness.

## Recommended MVP

Build a local-first Streamlit application in Python. Keep planning calculations and scheduling in ordinary Python modules so the interface can later be replaced by FastAPI plus a separate frontend.

The MVP should:

1. collect athlete, test, availability, lifting, coaching, race, and preference data;
2. calculate transparent working intensity guidance with confidence labels;
3. create an independent multi-duration power profile when valid tests are supplied;
4. build phases backward from A/B/C races;
5. schedule sessions without violating fixed constraints;
6. select only original session templates from `data/session_library.json`;
7. show assumptions, data-quality checks, and warnings before export;
8. create a downloadable `.xlsx` workbook;
9. save and reload a profile as JSON;
10. lock completed weeks and regenerate future weeks after a race or retest.

## Start here

Give Codex the entire folder or ZIP.

- For a new build, use `CODEX_PROMPT.md`.
- To update code already built from v0.1, use `CODEX_UPDATE_PROMPT.md`.

## Package map

- `CODEX_PROMPT.md`: full build prompt
- `CODEX_UPDATE_PROMPT.md`: focused v0.1 → v0.2 implementation prompt
- `docs/POWER_PROFILE_SPEC.md`: independent profiling algorithm and planning behavior
- `docs/PRODUCT_REQUIREMENTS.md`: scope and user flow
- `docs/DOMAIN_RULES.md`: deterministic planning and scheduling rules
- `docs/COPYRIGHT_AND_SOURCE_POLICY.md`: source and reuse policy
- `docs/WORKBOOK_SPEC.md`: required Excel output
- `docs/DECISIONS_AND_OPEN_QUESTIONS.md`: launch decisions and validation work
- `schemas/power_profile.schema.json`: derived profile contract
- `schemas/*.json`: remaining data contracts
- `data/session_library.json`: original workout-template starter library
- `data/sample_athlete.json`: acceptance-test profile
- `config/defaults.json`: editable planning defaults and feature flags
- `tests/ACCEPTANCE_TESTS.md`: functional criteria
- `sources/source_register.csv`: evidence and rights register

## Disclaimer

This is a training-planning product specification, not medical or legal advice. A qualified attorney should review licensing, terms, privacy, and liability before public or commercial launch. The independent power-profile rules are coaching-support heuristics, not validated diagnostic or performance-prediction equations. Public claims require coach review, athlete testing, and versioned validation.

---

# INITIAL CODEX PROMPT

# Initial Codex prompt — v0.2

Build the first working MVP described in this repository.

## Product

Create a local-first Python Streamlit application named **Rowing Plan Generator**. It must collect athlete details, test data, training availability, fixed lifting/coached sessions, race dates and priorities, and output a deterministic season plan plus a downloadable Excel workbook.

Do not scrape the web at runtime. Do not copy workouts, test instructions, descriptions, tables, formulas, or visual layouts from commercial or social-media-distributed training products. Use only the original templates and rules in this repository and the source metadata in `sources/source_register.csv`.

## Required repository structure

```text
app.py
rowing_plan/
  __init__.py
  models.py
  conversions.py
  intensity.py
  power_profile.py
  periodization.py
  scheduler.py
  session_selector.py
  validators.py
  workbook.py
  persistence.py
config/defaults.json
data/session_library.json
schemas/
tests/
```

## Interface

Use a six-step wizard or tabs:

1. Athlete and goals
2. Testing and intensity preferences
3. Weekly availability and fixed commitments
4. Race calendar
5. Plan assumptions, power profile, and warnings
6. Preview and download

Use Streamlit forms for grouped input and session state for navigation and unsaved profile data. Put the download control outside the form.

## Core inputs

Implement the contracts in `schemas/athlete_profile.schema.json`, `schemas/race.schema.json`, and `schemas/power_profile.schema.json`.

Support:

- measured LT1/LT2 heart-rate and/or power values;
- coach-defined UT band boundaries;
- current 2k erg time;
- resting and maximum heart rate;
- an optional short peak-power result, including a seven-stroke protocol label;
- optional 60-second average watts;
- optional 30-minute rate-capped average watts and rate cap;
- test date, erg model, drag factor, validity, and notes for each test;
- no current test, with a visible low-confidence provisional profile.

## Intensity hierarchy

Use this order for UT-band boundaries:

1. measured thresholds;
2. coach-defined bands;
3. 2k-based configurable defaults plus heart-rate-reserve fallback;
4. heart-rate-reserve and effort descriptors only.

The Multi-Duration Rowing Power Profile does not silently replace measured thresholds. It may supply session-specific PP/AN power anchors, plausibility checks, and provisional rate-capped references as defined in `docs/POWER_PROFILE_SPEC.md`.

Every calculated band must include `method`, `confidence`, and `assumptions`. Never present provisional values as measured thresholds.

Use the official Concept2 relationship:

```python
watts = 2.8 / (split_seconds_per_meter ** 3)
split_seconds_per_500m = 500 * (2.8 / watts) ** (1/3)
```

where `split_seconds_per_meter = split_seconds_per_500m / 500`.

## Independent Multi-Duration Rowing Power Profile

Implement `rowing_plan/power_profile.py` according to `docs/POWER_PROFILE_SPEC.md`.

Required behavior:

- raw test results are active planning inputs, not merely display fields;
- calculate transparent ratios among the athlete's own measurements;
- validate test order, recency, protocol metadata, and plausible monotonic power;
- create session-specific power anchors and ceilings;
- use HR/RPE as primary control for low-intensity work when no threshold or 2k power provider exists;
- never calculate a predicted 2k from a third-party weighted formula;
- do not automatically diagnose physiological systems;
- do not enable population-based weakness labels unless reference bands are explicitly configured and the feature flag is enabled;
- support longitudinal comparison after two or more valid batteries;
- show exactly how the profile changed session selection or target power.

## Copyright boundary

The test concepts and athlete results may be used. The implementation, names, explanations, ratio calculations, plan logic, interface, and workbook must be original to this app.

Do not:

- use a third party's name in the public feature name without permission;
- copy their test instructions or descriptive labels;
- reproduce their spreadsheet design or cell formulas;
- implement their exact weighted predicted-2k formula;
- imply endorsement or affiliation.

## Periodization

Build phases backward from race dates. Support A, B, and C priorities and multiple races on one weekend.

- A: primary peak; configurable 7–14 day taper and 2–5 day recovery.
- B: rehearsal; configurable 4–7 day reduced-load period and 1–3 day recovery.
- C: benchmark; minimal disruption, normally 1–3 reduced days.

Use configuration, not hard-coded claims, for exact taper percentages. Defaults must be visible in the assumptions panel.

## Scheduler

Place fixed events first, then races, rest days, coached sessions, lifting, quality sessions, low-intensity volume, and optional sessions.

Hard constraints:

- never schedule rowing on a day where `row_on_lifting_day` is false;
- never overwrite a fixed rest day;
- never schedule ordinary training on a race day;
- respect unavailable and travel days;
- do not exceed maximum sessions per day;
- locked/completed weeks must not change on regeneration.

Default soft constraints:

- no more than two high-intensity rows per week;
- avoid consecutive high-intensity rows;
- with three heavy lifting days, default to one high-intensity row unless explicitly overridden;
- place the hardest race-specific row where recovery is best;
- a row immediately before heavy lifting should normally be moderate rather than maximal;
- drop optional sessions before fixed or priority sessions;
- profile-based modifiers may not violate recovery constraints or race taper rules.

Every soft-constraint violation creates a warning.

## Session selection

Select from `data/session_library.json` by target band, phase, race distance, mode, available minutes, recovery cost, rate goal, broken-piece preference, and available power anchors.

Do not generate arbitrary workout text with an LLM. A template may be parameterized only within its allowed ranges.

Each generated erg session must record:

- `power_target_method`;
- source test or intensity provider;
- target watts and equivalent split when available;
- confidence and assumptions;
- any cap or drop-off rule used.

## On-water versus erg

Erg prescriptions show watts and 500m split together. On-water prescriptions prioritize rate, heart rate, effort descriptor, technical cue, and context-aware speed notes. Do not pretend on-water 500m split is directly comparable across current, wind, boat class, or direction.

## Workbook export

Implement `docs/WORKBOOK_SPEC.md` with openpyxl. Export a BytesIO object through Streamlit. Include plain-text URLs in the Sources sheet and a Power Profile sheet explaining measured values, ratios, active planning uses, and limitations.

## Validation and tests

Implement unit tests for conversions, intensity hierarchy, power-profile metrics, data-quality flags, power anchors, longitudinal changes, race phases, hard constraints, session filtering, and workbook generation. Implement `data/sample_athlete.json` and every check in `tests/ACCEPTANCE_TESTS.md`.

## Nonfunctional requirements

- deterministic output for identical inputs and config;
- no network dependency at runtime;
- type hints and docstrings;
- clear validation messages;
- no hidden medical diagnosis or injury treatment;
- accessible labels and keyboard-friendly navigation;
- separate UI code from planning logic;
- JSON configuration instead of embedded content;
- every algorithm and default has a version string;
- generated plans retain the algorithm version used.

## Deliverables

1. Running Streamlit app
2. Automated tests
3. Sample generated workbook
4. README with local installation and launch instructions
5. Implementation note listing assumptions, deviations, and risks
6. A change-summary view when future weeks are regenerated after a new test

First implement the deterministic engine and tests. Add the Streamlit UI only after the engine passes tests.

---

# V0.1 TO V0.2 UPDATE PROMPT

# Codex update prompt — upgrade v0.1 to v0.2

Update the existing Rowing Plan Generator implementation to use the v0.2 **Multi-Duration Rowing Power Profile**.

## Primary change

Replace all `profile_only` treatment of short-, 60-second-, and 30-minute rate-capped tests with active, transparent planning behavior defined in `docs/POWER_PROFILE_SPEC.md`.

The tests must affect plans through session-specific power anchors, plausibility checks, goal-relevant session selection, and longitudinal comparison. They must not produce an unlicensed predicted 2k or silently redefine LT1/LT2.

## Required code changes

1. Add `rowing_plan/power_profile.py`.
2. Implement models matching `schemas/power_profile.schema.json`.
3. Update athlete input models to support `multi_duration_power_tests` and protocol metadata.
4. Preserve legacy fields only for migration; convert them to the new structure on load.
5. Remove `profile_only` from generated outputs and intensity method enums.
6. Add session fields for `power_target_method`, source anchor, target watts, equivalent split, confidence, and assumptions.
7. Add a feature-flagged ratio classifier. It must remain inactive when no explicit reference bands are configured.
8. Add longitudinal comparison for two or more valid batteries.
9. Add a Power Profile UI panel and workbook sheet.
10. Add all v0.2 tests from `tests/ACCEPTANCE_TESTS.md`.

## Migration rules

Map legacy fields as follows:

- `seven_stroke_peak_watts` → `multi_duration_power_tests.short_peak.value_watts`, protocol `seven_stroke`;
- `sixty_second_avg_watts` → `multi_duration_power_tests.one_minute.value_watts`;
- `thirty_minute_r20_avg_watts` → `multi_duration_power_tests.rate_capped_sustained.value_watts`, duration 1800 seconds, rate cap 20.

Do not discard legacy values. Add a migration warning if test dates or protocol metadata are absent.

## Planning behavior

Use the measured values as follows:

- very short PP sessions may use the short peak test as an anchor;
- 30–120 second AN sessions may use the 60-second result as an anchor;
- rate-capped sustained sessions may use the 30-minute result as a reference and plausibility check;
- low-intensity UT band boundaries still come from measured thresholds, coach bands, 2k configuration, or HRR/RPE fallback;
- when no 2k or thresholds exist, do not pretend that the 30-minute test precisely identifies UT2 or LT1;
- explain all profile-driven changes in the plan.

## Rights and claims

Use the public feature name **Multi-Duration Rowing Power Profile**. Use original wording. Do not mention a third-party coach in the user-facing application unless separate written permission is supplied. Do not copy the source spreadsheet or exact formula.

## Completion criteria

The update is complete only when:

- JSON schemas validate;
- the sample athlete produces the expected ratios and active power anchors;
- no predicted 2k is generated;
- locked weeks remain unchanged after a new battery is entered;
- future weeks show a change summary;
- the workbook contains a Power Profile sheet;
- all v0.2 acceptance tests pass.

---

# PRODUCT REQUIREMENTS

# Product requirements document

## 1. Product purpose

The Rowing Plan Generator helps recreational, masters, and competitive rowers turn goals, test data, race dates, availability, lifting commitments, and coaching sessions into a transparent training schedule and downloadable workbook.

The product should behave as a planning assistant, not an autonomous medical professional or an opaque AI coach. The same inputs and configuration must produce the same plan.

## 2. Target users

### Primary

- masters rowers balancing work, rowing, and strength training;
- rowers preparing for head races, 1k sprints, or 2k erg tests;
- athletes who train both on water and on an erg;
- athletes who use UT3/UT2/UT1/AT/TR/AN/PP terminology;
- rowers who prefer broken aerobic pieces rather than one uninterrupted duration.

### Secondary

- coaches creating an initial schedule for an athlete;
- clubs that want a consistent planning template.

The MVP should be adult-only. Supporting minors requires separate consent and safeguarding design.

## 3. User jobs

A user should be able to:

1. enter current fitness and test data;
2. describe the season goal and race calendar;
3. mark fixed lifting, coached rows, work restrictions, and rest days;
4. choose erg/on-water availability and preferred effort displays;
5. review provisional intensity guidance, the Multi-Duration Rowing Power Profile, and confidence;
6. see exactly how test results affect targets and session selection;
7. preview phases and weekly sessions;
8. change assumptions and regenerate future weeks;
9. lock completed weeks;
10. download a formatted Excel plan;
11. save the profile as JSON for later use.

## 4. MVP scope

### Included

- one athlete and one season per profile;
- head-race, 1k sprint, 2k erg, technique, and general-fitness goals;
- A/B/C race priorities;
- multi-race weekends;
- fixed lifting days and optional alternate UT2 after lifting;
- fixed coaching/lesson days;
- fixed rest and unavailable days;
- UT terminology with editable definitions;
- measured threshold, coach-defined, 2k-based, and HRR fallback pathways;
- an independent Multi-Duration Rowing Power Profile with active session anchors and longitudinal comparison;
- feature-flagged coach/research ratio interpretation;
- watts/split conversion;
- original workout-template library;
- deterministic schedule and validators;
- Excel and JSON downloads.

### Deferred

- wearable, Garmin, Concept2 Logbook, or Strava imports;
- real-time adaptive prescription from daily readiness;
- team accounts, coach dashboards, payments, or subscriptions;
- in-app messaging;
- injury rehabilitation;
- nutritional plans;
- direct AI generation of new workouts;
- copied proprietary commercial testing formulas or branded implementations without permission;
- automatic population-based weakness labels without a documented reference set.

## 5. Main user flow

### Step 1 — Athlete and goals

Collect name or nickname, age band, experience, primary boat classes, season dates, primary goals, and race-rate goals.

### Step 2 — Testing

Ask what data are available. Explain the confidence hierarchy. Collect protocol metadata for short peak, one-minute, and rate-capped sustained tests. Let the athlete select watts, split, HR, or a combination as the preferred erg display. Explain that the independent power profile can personalize sessions without predicting a 2k or measuring thresholds.

### Step 3 — Weekly structure

Use a seven-day grid. Each day can include availability, maximum duration, lifting, alternate cardio, coached rowing, rowing mode, fixed rest, and notes. Ask whether rowing is possible on lifting days rather than assuming it.

### Step 4 — Races

Collect date, distance, race type, priority, boat class, expected number of races, travel, and whether the result should be used as a benchmark for later phases.

### Step 5 — Assumptions

Show intensity method, power-profile status, active anchors, confidence, weekly target, hard constraints, taper defaults, high-intensity limit, and warnings. Require acknowledgment before generating.

### Step 6 — Plan and download

Show season timeline, weekly calendar, training-band totals, unresolved warnings, and workbook download.

## 6. Success criteria

- A first-time user can create a plan in under 15 minutes.
- No fixed day or race constraint is violated without a visible blocking error.
- Every workout has a purpose, target band, duration, rate guidance, mode, recovery, and source-basis identifier.
- All erg sessions show equivalent watts and 500m split when enough data exist.
- The workbook opens without repair warnings and contains no broken formulas.
- A user can understand which values are measured, coach-defined, calculated, or provisional.

## 7. Safety and trust

- Display that the product is not medical advice.
- Ask about current medical restriction or unresolved injury only to determine whether automatic high-intensity planning should be disabled; do not diagnose.
- Provide a manual coach-review flag.
- Keep source metadata visible.
- Do not claim that one intensity distribution is universally superior.
- Treat athlete feedback and race outcomes as reasons to revise future weeks, not as proof of a diagnosis.


## 8. Power-profile product requirements

- A user can enter one, two, or all three multi-duration tests.
- The UI distinguishes raw results, descriptive ratios, session anchors, and intensity bands.
- Default mode does not apply population labels.
- A coach can add reference bands with provenance and versioning.
- The app shows every profile-driven plan change.
- A new test can regenerate only unlocked future weeks.
- No screen or export displays a predicted 2k unless it comes from a separately documented, permitted, and validated model.

---

# MULTI-DURATION POWER PROFILE

# Multi-Duration Rowing Power Profile specification

## 1. Purpose

The Multi-Duration Rowing Power Profile is an original, transparent coaching-support feature that uses an athlete's measured ergometer results at several durations to personalize a training plan.

It is not:

- a copied commercial test battery;
- a predicted-2k formula;
- a lactate or ventilatory threshold test;
- a medical assessment;
- a validated diagnosis of an athlete's aerobic or anaerobic system.

Its primary job is to connect measured performance to session selection, power targets, caps, and future retesting.

## 2. Inputs

The profile can use any subset of:

### Short peak test

- `value_watts`: best measured power;
- `protocol`: `seven_stroke`, `ten_second`, `other_short_peak`;
- `duration_seconds` when known;
- test date, erg model, drag factor, test validity, and notes.

### One-minute test

- `value_watts`: average power for 60 seconds;
- test date, erg model, drag factor, average rate, peak rate, validity, and notes.

### Rate-capped sustained test

- `value_watts`: average power;
- `duration_seconds`, normally 1800;
- `rate_cap_spm`, such as 20;
- average and maximum HR when available;
- test date, erg model, drag factor, validity, and notes.

### Optional integrated performance tests

- actual 2k seconds and average watts;
- measured LT1/LT2 HR or power;
- coach-defined UT bands.

The app must preserve protocol metadata so unlike tests are not treated as interchangeable.

## 3. Data-quality validation

A profile can be `complete`, `partial`, `invalid`, or `stale`.

Required checks:

1. all watt values are positive;
2. short peak power should normally exceed one-minute average power;
3. one-minute average power should normally exceed 30-minute rate-capped power;
4. test dates must be within the configured recency window for active planning;
5. a test explicitly marked invalid is stored but not used;
6. major differences in erg model or drag factor create a comparison warning;
7. missing protocol metadata lowers confidence;
8. values that violate expected ordering require confirmation and must not create automatic modifiers until resolved.

Expected ordering is a data-quality heuristic, not proof of validity.

## 4. Derived metrics

When the necessary values are valid, calculate:

```text
one_minute_retention = one_minute_watts / short_peak_watts
rate_capped_endurance_retention = sustained_watts / one_minute_watts
sustained_to_peak_ratio = sustained_watts / short_peak_watts
```

When an actual 2k is available, also calculate:

```text
peak_to_2k_ratio = short_peak_watts / two_k_watts
one_minute_to_2k_ratio = one_minute_watts / two_k_watts
sustained_to_2k_ratio = sustained_watts / two_k_watts
```

Round only for display. Store full precision.

These ratios are descriptive relationships among the athlete's own measurements. They are not population percentiles and do not receive automatic labels such as good, poor, aerobic, or anaerobic unless an explicit reference set is configured.

## 5. Active planning uses

### 5.1 Very short peak-power work

When a valid short peak value exists, PP and start sessions may use it as a session-specific anchor.

Default pilot rule:

- target range for repeatable very short work: configurable percentage of measured short peak;
- never prescribe above measured peak by default;
- stop or extend recovery when output drops beyond the configured quality-loss limit;
- HR is not used as the primary control.

The default percentages are app configuration, not scientific thresholds. Show them to the user and permit coach override.

### 5.2 Thirty- to 120-second high-intensity work

When a valid one-minute average exists, AN sessions may use it as an anchor.

The selected target range depends on repetition duration:

- shorter than 60 seconds may be near or above one-minute average but below the short peak ceiling;
- approximately 60 seconds may be anchored near the one-minute result;
- longer than 60 seconds must use a lower percentage of the one-minute result.

All percentage ranges are configurable and identified as pilot coaching defaults.

### 5.3 Rate-capped sustained work

A valid 30-minute rate-capped result may:

- anchor future repetitions of the same or closely related test protocol;
- check whether sustained erg prescriptions are plausible;
- help set broad provisional watt references for rate-capped endurance pieces when no 2k or threshold power is available;
- support longitudinal monitoring.

It must not be presented as a direct measurement of LT1, LT2, UT2, or AT.

When the athlete has no 2k, measured threshold, or coach-defined power bands:

- HRR, breathing, RPE, and stroke rate remain the primary controls for UT3/UT2/UT1;
- the sustained test may provide a broad secondary reference only;
- exact low-intensity watt bands remain disabled unless the user or coach explicitly enables the provisional rule.

### 5.4 Plausibility checks

Flag sessions when:

- PP target exceeds measured short peak;
- 60-second target exceeds both the one-minute anchor and short peak ceiling;
- a long rate-capped target substantially exceeds the measured sustained result without a documented progression reason;
- prescribed watt ordering conflicts with the athlete's measured power-duration ordering.

### 5.5 Session eligibility and selection

The profile can influence which templates are eligible:

- PP sessions requiring a power target become fully personalized when short peak data exist;
- AN sessions become fully personalized when one-minute data exist;
- rate-capped endurance sessions can show a measured reference when sustained data exist;
- missing data do not block training, but targets fall back to rate, RPE, HR, split from another provider, or coach entry.

Goal and race phase remain the primary drivers of session frequency. The power profile refines rather than replaces periodization.

## 6. Profile interpretation modes

### Mode A — anchors only

Default public mode.

- uses measured values for targets, caps, eligibility, and longitudinal comparison;
- calculates descriptive ratios;
- does not label a weakness from a single battery;
- does not use population reference bands.

### Mode B — coach-defined reference bands

A coach can provide explicit ranges for the derived ratios. The app may then create domain flags and planning modifiers. The source, population, date, and reviewer must be recorded.

### Mode C — research/pilot classifier

Feature-flagged and off by default.

This mode can apply versioned reference ranges developed from a consented calibration dataset. The app must show:

- dataset population;
- sample size;
- model version;
- uncertainty;
- whether the athlete falls outside the represented population.

Do not ship generic ratio thresholds with unsupported labels.

## 7. Planning modifiers when a classifier is enabled

A classifier may suggest, but not force:

### Relative short peak limitation

- add a low-volume PP exposure;
- preserve heavy strength training;
- use full recovery and quality cutoffs;
- do not replace required low-intensity volume with large glycolytic volume.

### Relative one-minute limitation

- add 30–90 second AN development;
- include race starts and transition-to-pace work for sprint goals;
- progress total high-intensity work conservatively.

### Relative rate-capped sustained limitation

- emphasize UT2, UT1, and controlled AT;
- use broken aerobic sessions;
- increase goal-rate duration gradually;
- delay large race-rate volume when technique or durability deteriorates.

### Balanced profile

- use goal- and race-driven distribution without a corrective emphasis.

Modifiers may shift no more than the configured percentage of quality minutes in one block and may never override race taper, fixed recovery constraints, or maximum hard-session limits.

## 8. Longitudinal comparison

After two or more valid batteries using comparable protocols:

1. calculate percentage change for each measure;
2. show raw and percentage changes;
3. apply a configurable measurement-noise threshold before calling a change meaningful;
4. compare changes with the athlete's goal and recent training;
5. allow a small next-block emphasis shift when one goal-relevant measure declines or fails to respond;
6. require a visible explanation and user/coach acceptance.

A single retest should not rewrite completed weeks. Only unlocked future weeks may change.

## 9. Relationship to intensity bands

The power profile and intensity provider are separate components.

- measured thresholds or coach bands define intensity when available;
- 2k configuration may define provisional erg bands;
- HRR/RPE may define provisional cardiovascular bands;
- the power profile supplies session-specific anchors and checks;
- a generated session can therefore have an HRR-derived UT2 band and a separate sustained-power reference.

Do not merge these concepts into a false-precision zone table.

## 10. Explainability requirements

For every profile-influenced session, store and display:

- input test used;
- test date and validity;
- calculation or percentage applied;
- resulting target or ceiling;
- confidence;
- reason the session was selected or changed;
- applicable caveat.

Example:

> Target power is anchored to 85–95% of the athlete's valid short peak test. This is a configurable pilot coaching rule, not a measured physiological threshold.

## 11. Algorithm versioning

Store:

- `power_profile_algorithm_version`;
- configuration version;
- source-register version;
- classifier version, if any;
- test battery IDs;
- generated-plan timestamp.

A new algorithm version must not silently alter locked plans.

## 12. Sample-athlete expected values

For short peak 287 W, one-minute average 220 W, and 30-minute rate-20 average 143.8 W:

```text
one_minute_retention = 220 / 287 = 0.7665505...
rate_capped_endurance_retention = 143.8 / 220 = 0.6536363...
sustained_to_peak_ratio = 143.8 / 287 = 0.5010452...
```

Expected v0.2 behavior:

- profile status is usable but provisional because no actual 2k or measured thresholds exist;
- PP and AN anchors are active;
- the sustained result is an active reference and plausibility check;
- UT2/UT1 control remains primarily HRR/RPE unless the user enables a provisional power rule;
- no predicted 2k is produced;
- no automatic weakness label is produced in anchors-only mode.

---

# DOMAIN RULES

# Domain and scheduling rules

## 1. Core concepts

### Bands

The user-facing vocabulary is UT3, UT2, UT1, AT, TR, AN, and PP. The app must keep the band definitions configurable because organizations and coaches use different boundaries.

Internally, each band includes:

- `name`
- `physiological_domain` (low, moderate, high, sprint/peak)
- `hr_low`, `hr_high`
- `watts_low`, `watts_high`
- `split_fast`, `split_slow`
- `spm_low`, `spm_high`
- `effort_low`, `effort_high`
- `method`
- `confidence`
- `assumptions`

### Confidence

- `high`: directly measured threshold or coach-entered tested band.
- `medium`: recent 2k plus valid max/rest HR, or a valid multi-duration profile used for session-specific anchors.
- `low`: HRR/effort fallback, stale/incomplete testing, or provisional power references without thresholds.

## 2. Conversions

### 2k time to average watts

1. Convert 2k time to total seconds.
2. Divide by four for 500m split seconds.
3. Convert split to watts with the Concept2 relationship.

### Watts to split

Use the inverse Concept2 relationship. Store calculations at full precision and format the display to tenths of a second only where useful.

### Heart-rate reserve

`HRR = max_hr - resting_hr`

`target_hr = resting_hr + HRR * fraction`

HRR defaults are fallback guidance, not measured lactate or ventilatory thresholds.

## 3. Intensity-provider hierarchy

### Provider A: measured thresholds

Inputs can include LT1/LT2 HR and power. The app maps configurable UT bands around these boundaries. This is the preferred provider.

### Provider B: coach-defined bands

The coach or athlete enters each band's HR and/or power boundaries directly. Validate that ranges are ordered and non-overlapping unless an overlap is intentionally allowed.

### Provider C: 2k-config profile

Use a configurable percentage profile for low through transport work. Do not present these values as universal physiology. Display the profile name and source notes.

### Provider D: HRR and effort fallback

Use HRR plus talk/effort descriptors. Disable exact erg watts unless a 2k or coach-defined power boundary exists.

### Multi-Duration Rowing Power Profile

The short peak, 60-second, and rate-capped sustained tests are active planning inputs under `docs/POWER_PROFILE_SPEC.md`. They may:

- create session-specific PP and AN power anchors;
- provide a rate-capped sustained reference and plausibility checks;
- calculate descriptive within-athlete ratios;
- influence template eligibility and goal-relevant session selection;
- support longitudinal comparisons and future-block adjustments;
- support a feature-flagged classifier only when explicit reference bands exist.

They must not:

- feed a copied third-party weighted predicted-2k formula;
- silently redefine LT1/LT2 or exact UT bands;
- create diagnostic labels from a single battery in default mode;
- override fixed recovery, taper, or maximum hard-session rules.

The default public mode is `anchors_only`.

## 4. Time accounting

Track at least three totals:

1. cardiovascular minutes by training band;
2. rowing-specific minutes by band;
3. strength-training sessions and optional minutes.

Post-lifting treadmill or elliptical UT2 contributes to cardiovascular UT2 but not rowing-specific UT2.

Count the actual work prescription by band, not the whole session label. Warm-up, cool-down, and easy recovery are normally low-intensity minutes.

## 5. Phase construction

Create phase boundaries backward from prioritized races, then fill earlier dates.

Suggested phase types:

- transition/re-entry;
- general preparation;
- specific preparation;
- race build;
- taper/sharpen;
- race/recovery.

Multiple races in one weekend are one race event with multiple race days and higher recovery demand.

Race-priority behavior is configurable:

- C: benchmark, minimal taper, normal training resumes after brief recovery.
- B: rehearsal, moderate reduction, practice warm-up/pacing/recovery.
- A: peak, largest reduction, preserve intensity, protect travel and multi-race recovery.

## 6. Weekly load pattern

The default block pattern is configurable. A 3-build/1-deload pattern may be used, but the app must not imply it is the only valid model.

Weekly target calculation should use:

- current sustainable volume;
- user-entered peak volume;
- fixed race and travel constraints;
- phase multiplier;
- deload multiplier;
- completed training and athlete feedback, when available.

Do not add missed sessions to later days automatically.

## 7. Session placement order

1. race and travel days;
2. fixed rest/unavailable days;
3. fixed coached rows;
4. fixed lifting and alternate cardio;
5. taper/recovery protections;
6. primary quality row;
7. long low-intensity row;
8. secondary moderate or quality row;
9. optional low-intensity/technique row.

## 8. Hard constraints

A hard-constraint failure blocks export unless the user changes the input:

- rowing on a prohibited lifting day;
- training on a fixed rest or unavailable day;
- ordinary training on a race day;
- session longer than the day's maximum available duration;
- more than the maximum sessions per day;
- changed content inside a locked week;
- race taper beginning before the season start;
- invalid date sequence;
- invalid intensity boundaries.

## 9. Soft constraints and warnings

Generate a warning when:

- more than two high-intensity rowing sessions occur in one week;
- high-intensity rowing is scheduled on consecutive days;
- a maximal rowing session occurs the day before heavy lifting;
- a high-recovery-cost session occurs within 48 hours of an A race;
- weekly endurance minutes change more than the configured limit;
- a low-confidence zone profile is used for exact high-intensity targets;
- the athlete has three heavy lifting days and two hard rows;
- a coached session's expected intensity is unknown;
- post-lifting cardio repeatedly consumes most low-intensity volume but rowing-specific volume is insufficient.

Warnings are explainable and editable. They are not diagnoses.

## 10. Race-specific logic

### 5k head race

Progress from technical low-rate work to controlled UT1/AT, then rate-specific TR work. The user can enter a goal rate such as 30–32 spm. The plan should build the duration at goal rate gradually and preserve technique.

### 1k sprint

Include start practice, short high-rate repetitions, and selected longer race-specific pieces. A user-entered goal such as 34–35 spm is a target, not a universal standard.

### 2k erg

Use watts and split targets, with rate and RPE. Race-specific work can increase as the event approaches.

## 11. On-water prescription

Primary fields:

- band;
- target rate;
- effort/breathing descriptor;
- HR guidance where available;
- piece structure and recovery;
- technical cue;
- direction/current/wind note.

Do not use on-water 500m split as a universal effort target. It may be recorded for same-course comparisons.

## 12. Regeneration

When test or race data change:

- preserve locked/completed weeks;
- rebuild only future phases;
- retain fixed events;
- show a change summary;
- do not silently alter user-entered bands;
- mark the reason for each changed key session.

## 13. Profile-driven planning order

When a valid Multi-Duration Rowing Power Profile exists:

1. determine the phase and goal demands;
2. select eligible original session templates;
3. apply the intensity-provider band;
4. apply measured session-specific power anchors or ceilings;
5. run plausibility and recovery checks;
6. record an explanation of every profile-driven change;
7. reject any modifier that violates a hard constraint.

Profile data refine the plan; they do not supersede race periodization or athlete availability.

---

# COPYRIGHT AND SOURCE POLICY

# Copyright, licensing, and evidence-source policy

## 1. Product rule

The app may use facts, scientific findings, mathematical relationships, training concepts, and methods, but it must create its own wording, organization, session names, templates, explanations, and workbook presentation.

U.S. copyright law distinguishes ideas, procedures, processes, systems, and methods from the particular expression used to describe them. That distinction does **not** provide permission to copy a commercial plan's wording, tables, selection, arrangement, branding, screenshots, or spreadsheet design. Treat all third-party content as protected unless its license or permission is documented.

This document is a conservative product policy, not legal advice.

## 2. Allowed source classes

### Preferred

- peer-reviewed research used for findings, not copied prose;
- official equipment-manufacturer formulas and technical documentation;
- official government publications;
- CC0/public-domain material;
- CC BY material with compliant attribution;
- original sessions written for this project and tagged with evidence sources.

### Permission required

- commercial coaching spreadsheets;
- paid plans, membership-platform workouts, books, videos, or newsletters;
- branded test-battery wording, proprietary prediction formulas, and copied spreadsheet implementations;
- tables or diagrams from papers or websites;
- substantial copied text even when publicly viewable;
- ND-licensed material that would be adapted.

### Excluded by default

- unattributed social-media posts;
- scraped workout databases;
- copied plan calendars;
- training content with unclear ownership;
- AI-generated summaries that lack a traceable source.

## 3. How to build the session library

Each app session must be an original expression. It may be informed by one or more source findings, for example:

- most seasonal rowing volume is low intensity;
- interval training can improve rowing performance;
- training distribution can shift across a season;
- tapering generally reduces volume while preserving some intensity;
- actual time in zone is useful for load accounting.

Do not copy the exact title, prose, order, commentary, or full plan structure of a source. When an exact research protocol is retained for evidence or testing, label it as a cited research protocol and do not present it as proprietary app content.

## 4. Source register requirements

Every source must have:

- source ID;
- title, author/organization, and year;
- stable URL and DOI when available;
- source type;
- license/access status;
- date checked;
- evidence area;
- permitted app use;
- prohibited or cautionary use;
- notes on population and generalizability.

## 5. Attribution

The generated workbook's Sources sheet should cite the evidence basis and technical formulas. It should not imply endorsement by authors, journals, governing bodies, or equipment manufacturers.

For CC BY content, preserve creator, title, source, license, and modification notice. For CC0 content, attribution is not legally required by CC0, but a scholarly source note is still good practice.

## 6. Specific decision about the uploaded coaching calculator

The MVP may accept the athlete's 7-stroke, 60-second, and 30-minute rate-20 results as data. It must not reproduce the calculator's exact instructions, formulas, design, or wording unless the rights owner grants permission.

Without separate permission:

- do not use the creator's name as the public feature name;
- do not implement a weighted predicted-2k calculation copied from the workbook;
- do not copy test explanations, labels, zone tables, formulas, cell structure, or design;
- do not imply endorsement or affiliation.

The app may independently collect athlete-owned results from generic short peak, one-minute, and rate-capped sustained tests and use them through the original Multi-Duration Rowing Power Profile. The independent calculations, names, interpretations, planning rules, user interface, and workbook presentation must be documented in this repository and supported by independent evidence where available.

Copyright policy does not establish that every method is free of all other legal restrictions. Obtain legal review before commercial launch.

## 7. Release checklist

Before a source-backed feature ships:

1. source entry exists;
2. license/access is recorded;
3. implementation uses facts/methods or permitted content only;
4. all wording is original or properly attributed;
5. no screenshot/table/design was copied;
6. source limitations are represented;
7. a human reviewer approves the entry.


## 8. Independent-algorithm documentation rule

For every algorithm inspired by general training concepts:

1. document the purpose and inputs without copying source language;
2. cite independent research supporting the relevance of the measurements;
3. distinguish validated findings from app-created heuristics;
4. version every formula and configuration;
5. prohibit claims of endorsement;
6. retain a human-readable audit trail showing how the plan used each input.

---

# WORKBOOK SPECIFICATION

# Excel workbook specification

The workbook should be generated from a clean app-owned template, not by redistributing or modifying a commercial training spreadsheet.

## Sheet 1 — START HERE

Purpose, disclaimer, athlete name, season dates, plan generation date, intensity method, confidence level, major assumptions, and color legend.

## Sheet 2 — ATHLETE PROFILE

Inputs and derived values:

- goals and experience;
- available test data;
- resting/max HR;
- 2k time, split, and watts;
- multi-duration power-test results and protocol metadata;
- race-rate goals;
- weekly constraints;
- calculation method and confidence.

Editable inputs should be visually distinct. Derived values should use formulas where feasible.

## Sheet 3 — POWER PROFILE

Columns/sections:

- test type and protocol;
- date, erg model, drag factor, validity, and confidence;
- measured watts and equivalent split where meaningful;
- within-athlete ratios;
- active PP/AN/sustained anchors;
- planning uses and sessions affected;
- longitudinal change when previous batteries exist;
- warnings and limitations;
- algorithm and configuration version.

Do not display a predicted 2k from an excluded third-party formula.

## Sheet 4 — TRAINING BANDS

Columns:

- band;
- physiological domain;
- HR range;
- watts;
- 500m split;
- rate;
- effort descriptor;
- primary use;
- method;
- confidence;
- cautions.

## Sheet 5 — SEASON OVERVIEW

Timeline of phases, race events, priorities, weekly target minutes, strength frequency, primary focus, and taper/recovery notes.

## Sheet 6 — DAILY SCHEDULE

Columns:

- week;
- date;
- day;
- phase;
- fixed/optional;
- mode;
- session ID;
- session/focus;
- total cardio minutes;
- rowing-specific minutes;
- quality minutes;
- primary band;
- HR guide;
- watts/split guide;
- power target method and source anchor;
- rate guide;
- structure;
- recovery;
- technical cue;
- adjustment/substitution;
- warning;
- completion status;
- actual notes.

## Sheet 7 — WEEKLY TOTALS

Planned and actual minutes by band, rowing-specific low-intensity minutes, cross-training low-intensity minutes, strength sessions, quality sessions, and warning status.

## Sheet 8 — SESSION LIBRARY

Include the app-owned session templates used in the season plus alternatives. Each row includes source-basis IDs and an `Original app template` rights note.

## Sheet 9 — RACE PLAN

Race date, priority, boat, distance, number of races, taper, warm-up placeholder, rate goal, pacing notes, travel, recovery, and post-race observations.

## Sheet 10 — WEEKLY LOG

Simple entry area for completed minutes, average HR, average watts/split, average rate, session RPE, sleep/recovery note, and coach comments.

## Sheet 11 — SOURCES

Plain-text URLs, source IDs, citation, evidence area, access/license, and notes. Do not paste article abstracts or copyrighted tables.

## Formatting

- freeze headers;
- filter schedule and library tables;
- readable column widths and wrapped text;
- dates as true Excel dates;
- minutes as numbers;
- no formula errors;
- no hidden external workbook links;
- no macros in MVP;
- workbook opens in Excel and LibreOffice without a repair prompt.

---

# DECISIONS AND OPEN QUESTIONS

# Decisions and open questions

## Decisions already made

- Deterministic rules first; AI is not required for plan generation.
- Streamlit is the fastest MVP interface.
- Python modules remain UI-independent.
- Excel is the primary deliverable.
- UT terminology is supported and configurable.
- Erg output shows watts and split together.
- On-water output uses rate, HR/effort, structure, and technique.
- Actual time in band is tracked separately from session labels.
- Cross-training UT2 after lifting counts cardiovascular time but not rowing-specific time.
- Commercial plan text, design, branding, and unlicensed formulas are excluded.
- Multi-duration test results are active planning inputs through an independent algorithm.
- The public default is anchors-only; population weakness labels are off until a reference set is documented.
- No third-party weighted predicted-2k formula is used.

## Open product decisions

1. **Zone defaults:** Which coach or advisory group will approve the public default UT power percentages?
2. **Reference data:** What consented dataset and coach panel will be used to calibrate any future ratio classifier?
3. **Licensing:** Will permission be requested from any commercial coach for branded attribution or a separate licensed formula?
4. **Audience:** Masters rowers only at first, or all adult rowers?
5. **Volume:** Should users enter current sustainable weekly volume, annual hours, or both?
6. **Readiness:** Will completed-session RPE and recovery notes influence future plans in version 1.1?
7. **Coach mode:** Who can override warnings, and how is that override recorded?
8. **Workbook design:** Build a new brand and layout or license an existing visual template?
9. **Storage:** Ephemeral/local profiles only or optional accounts?
10. **Business model:** Free open tool, paid download, club license, or coaching lead generator?

## Validation work before public release

- Have at least two qualified rowing coaches review rules and session templates.
- Pilot with rowers of different ages, experience, sex, goals, and weekly availability.
- Compare generated plans against coach-created plans without treating agreement as proof of correctness.
- Review accessibility and usability.
- Obtain legal review for copyright, terms, privacy, and liability.
- Record versioned changes to source evidence and defaults.


## Power-profile validation gates

Before enabling population-based profile labels by default:

1. define a target population;
2. collect comparable test protocols with consent;
3. quantify test-retest reliability;
4. establish reference distributions and uncertainty;
5. perform out-of-sample validation;
6. review with qualified rowing coaches and a sport scientist;
7. publish model version and limitations;
8. retain an anchors-only option.

---

# ACCEPTANCE TESTS

# Acceptance tests — v0.2

## A. Conversion tests

1. A 2k time of 8:00 produces a 2:00/500m split and approximately 202.5 W.
2. Converting that watt value back to split returns 2:00/500m within rounding tolerance.
3. Invalid zero or negative watts create validation errors.

## B. Intensity hierarchy

1. Measured LT1/LT2 values override all fallback calculations.
2. Coach-defined bands override 2k and HRR defaults.
3. A 2k profile is not used when disabled unless the user selects it.
4. The sample athlete receives low-confidence provisional low-intensity guidance because no 2k or thresholds are available.
5. The Multi-Duration Rowing Power Profile does not silently create LT1/LT2 values.

## C. Multi-Duration Rowing Power Profile

Using the sample athlete:

1. `one_minute_retention` equals `220 / 287` within floating-point tolerance.
2. `rate_capped_endurance_retention` equals `143.8 / 220`.
3. `sustained_to_peak_ratio` equals `143.8 / 287`.
4. Status is `partial` or `complete` according to the implemented completeness rule, but it is usable for active anchors.
5. A PP anchor is created from the 287 W short peak result.
6. An AN anchor is created from the 220 W one-minute result.
7. A rate-capped sustained reference is created from 143.8 W.
8. No predicted 2k watts or time is created.
9. Default `anchors_only` mode creates no population weakness label.
10. The provisional low-intensity watt rule remains disabled.
11. HRR/RPE remains the primary low-intensity control.
12. A warning is produced when drag factor or protocol metadata required for comparison is missing.
13. If short peak ≤ one-minute watts or one-minute ≤ sustained watts, the app requests confirmation and suppresses automatic modifiers.
14. Every anchor includes source test, formula/config percentage, confidence, and assumptions.
15. A generated PP target may not exceed short peak by default.
16. A generated AN target may not exceed the short peak ceiling.

## D. Optional classifier

1. The ratio classifier is disabled when `reference_bands` is null.
2. Injecting a test reference set produces deterministic domain flags.
3. Classifier output records reference-set ID, version, population, and uncertainty.
4. A planning modifier cannot shift more quality minutes than the configured maximum.
5. A modifier cannot create a third hard rowing session or violate taper rules.

## E. Longitudinal profile

1. Add a second comparable battery and calculate change for all available measures.
2. Changes below the configured noise threshold are labeled inconclusive/stable, not meaningful improvement or decline.
3. A goal-relevant decline may suggest at most one added domain exposure in the next block.
4. The user sees the reason for the suggestion.
5. Locked weeks remain byte-for-byte unchanged.

## F. Sample-athlete schedule

1. No rowing is scheduled on Monday or Friday.
2. Monday and Friday may contain heavy lifting plus treadmill/elliptical UT2.
3. Wednesday is the fixed coached on-water row unless it is a race/travel day.
4. Saturday contains no training.
5. Sunday is normally a longer low-intensity row and is not the routine maximal session.
6. Tuesday is the preferred primary quality-row slot.
7. Thursday is optional or low priority and is the first session removed when weekly load is reduced.
8. September 26 is C priority with minimal reduction.
9. October 17 is B priority with moderate reduction.
10. November 7–8 is one A event with two race days and three expected races.
11. A taper is larger than B, and B is larger than C.
12. No ordinary workout is scheduled on race days.
13. A warning appears if two hard rows plus three heavy lifting days are forced.

## G. Session-library tests

1. A head-race build week can select HEAD or TR templates.
2. A 1k sprint build can select SPRINT, AN, or PP templates.
3. A 50-minute UT2 slot returns only templates allowing 50 minutes.
4. On-water templates do not require watts.
5. Every selected template has a source-basis ID and `rights_status=original_app_template`.
6. No session text is generated outside approved template fields.
7. Profile-aware erg sessions record a target method and source anchor.

## H. Workbook tests

1. Export creates all required sheets, including POWER PROFILE.
2. Dates are true spreadsheet dates.
3. Source URLs are plain text or hyperlinks; no abstracts are copied.
4. Workbook contains no external links to source workbooks.
5. Workbook opens with openpyxl after save.
6. No formula cell contains a standard Excel error.
7. Filename is sanitized and ends in `.xlsx`.
8. The Power Profile sheet states that ratios are descriptive and no predicted 2k was generated.
9. Algorithm and config versions appear in the workbook.

## I. Regeneration tests

1. Lock the first two weeks, add a new 2k or power-profile battery, and regenerate.
2. Locked weeks are byte-for-byte unchanged in plan JSON.
3. Future guidance and erg targets update where applicable.
4. Fixed races, lifting days, Wednesday coaching, and Saturday rest remain unchanged.
5. The app displays a change summary identifying test inputs and sessions affected.

---

# DEFAULT CONFIGURATION

```json
{
  "config_version": "0.2",
  "intensity": {
    "hrr_fallback": {
      "UT3": [
        0.5,
        0.6
      ],
      "UT2": [
        0.6,
        0.7
      ],
      "UT1": [
        0.7,
        0.8
      ],
      "AT": [
        0.8,
        0.85
      ],
      "TR": [
        0.85,
        0.95
      ],
      "AN": [
        0.95,
        1.0
      ],
      "PP": null,
      "status": "provisional_app_default_not_measured_threshold"
    },
    "two_k_power_profile": {
      "enabled_by_default": false,
      "bands": {
        "UT3": [
          0.45,
          0.5
        ],
        "UT2": [
          0.51,
          0.6
        ],
        "UT1": [
          0.6,
          0.7
        ],
        "AT": [
          0.7,
          0.8
        ],
        "TR": [
          0.8,
          0.9
        ]
      },
      "status": "requires_coach_validation_before_public_launch"
    },
    "default_spm": {
      "UT3": [
        16,
        18
      ],
      "UT2": [
        18,
        20
      ],
      "UT1": [
        20,
        22
      ],
      "AT": [
        24,
        28
      ],
      "TR": [
        28,
        32
      ],
      "AN": [
        34,
        38
      ],
      "PP": [
        38,
        46
      ]
    }
  },
  "periodization": {
    "A": {
      "taper_days": [
        7,
        14
      ],
      "recovery_days": [
        2,
        5
      ]
    },
    "B": {
      "taper_days": [
        4,
        7
      ],
      "recovery_days": [
        1,
        3
      ]
    },
    "C": {
      "taper_days": [
        1,
        3
      ],
      "recovery_days": [
        1,
        2
      ]
    },
    "taper_volume_reduction_range": [
      0.41,
      0.6
    ],
    "maintain_some_intensity": true,
    "taper_values_are_configurable": true
  },
  "scheduler": {
    "max_high_intensity_rows_per_week": 2,
    "max_high_intensity_rows_with_three_heavy_lifts": 1,
    "avoid_consecutive_high_intensity_rows": true,
    "warn_if_high_recovery_cost_within_hours_of_A_race": 48,
    "default_weekly_change_warning_pct": 10,
    "do_not_make_up_missed_sessions": true
  },
  "workbook": {
    "include_sources": true,
    "include_session_library": true,
    "include_weekly_log": true
  },
  "power_profile": {
    "algorithm_version": "mdrpp-0.2.0",
    "default_mode": "anchors_only",
    "active_test_recency_days": 180,
    "comparison_drag_factor_warning_difference": 15,
    "quality_loss_limit_pct": 0.1,
    "anchors": {
      "pp_repeatable_pct_of_short_peak": [
        0.85,
        0.95
      ],
      "an_under_60s_pct_of_one_minute": [
        0.95,
        1.08
      ],
      "an_60s_pct_of_one_minute": [
        0.9,
        1.0
      ],
      "an_61_to_120s_pct_of_one_minute": [
        0.72,
        0.9
      ],
      "provisional_rate_capped_reference_pct_of_sustained": [
        0.7,
        0.9
      ]
    },
    "provisional_low_intensity_power_from_sustained_enabled_by_default": false,
    "ratio_classifier": {
      "enabled_by_default": false,
      "reference_set_id": null,
      "reference_bands": null,
      "max_quality_minutes_shift_pct_per_block": 0.1
    },
    "longitudinal": {
      "enabled": true,
      "minimum_comparable_batteries": 2,
      "meaningful_change_pct": 0.03,
      "maximum_added_domain_exposures_per_block": 1
    },
    "status": "pilot_coaching_defaults_require_validation"
  }
}
```

---

# POWER PROFILE SCHEMA

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "power_profile.schema.json",
  "title": "MultiDurationRowingPowerProfile",
  "type": "object",
  "required": [
    "algorithm_version",
    "status",
    "mode",
    "metrics",
    "anchors",
    "warnings",
    "assumptions"
  ],
  "properties": {
    "algorithm_version": {
      "type": "string"
    },
    "battery_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "status": {
      "enum": [
        "complete",
        "partial",
        "invalid",
        "stale",
        "unavailable"
      ]
    },
    "mode": {
      "enum": [
        "anchors_only",
        "coach_defined_reference",
        "research_pilot"
      ]
    },
    "confidence": {
      "enum": [
        "high",
        "medium",
        "low",
        "unavailable"
      ]
    },
    "metrics": {
      "type": "object",
      "properties": {
        "one_minute_retention": {
          "type": [
            "number",
            "null"
          ]
        },
        "rate_capped_endurance_retention": {
          "type": [
            "number",
            "null"
          ]
        },
        "sustained_to_peak_ratio": {
          "type": [
            "number",
            "null"
          ]
        },
        "peak_to_2k_ratio": {
          "type": [
            "number",
            "null"
          ]
        },
        "one_minute_to_2k_ratio": {
          "type": [
            "number",
            "null"
          ]
        },
        "sustained_to_2k_ratio": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": false
    },
    "anchors": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "domain",
          "source_test",
          "target_method",
          "confidence"
        ],
        "properties": {
          "domain": {
            "enum": [
              "PP",
              "AN",
              "RATE_CAPPED_SUSTAINED"
            ]
          },
          "source_test": {
            "enum": [
              "short_peak",
              "one_minute",
              "rate_capped_sustained"
            ]
          },
          "source_watts": {
            "type": "number",
            "exclusiveMinimum": 0
          },
          "target_method": {
            "type": "string"
          },
          "target_watts_low": {
            "type": [
              "number",
              "null"
            ]
          },
          "target_watts_high": {
            "type": [
              "number",
              "null"
            ]
          },
          "ceiling_watts": {
            "type": [
              "number",
              "null"
            ]
          },
          "confidence": {
            "enum": [
              "high",
              "medium",
              "low"
            ]
          },
          "assumptions": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "additionalProperties": false
      }
    },
    "interpretations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "domain",
          "state",
          "basis",
          "planning_modifier"
        ],
        "properties": {
          "domain": {
            "enum": [
              "short_peak",
              "one_minute",
              "rate_capped_sustained",
              "balanced"
            ]
          },
          "state": {
            "enum": [
              "not_classified",
              "relative_limitation",
              "relative_strength",
              "balanced",
              "insufficient_data"
            ]
          },
          "basis": {
            "type": "string"
          },
          "planning_modifier": {
            "type": "object"
          }
        },
        "additionalProperties": false
      }
    },
    "longitudinal_changes": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "assumptions": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "source_ids": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "additionalProperties": false
}
```

---

# ATHLETE PROFILE SCHEMA

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "athlete_profile.schema.json",
  "title": "RowingPlanAthleteProfile",
  "type": "object",
  "required": [
    "profile_version",
    "athlete",
    "season",
    "goals",
    "weekly_availability",
    "races",
    "preferences"
  ],
  "properties": {
    "profile_version": {
      "const": "0.2"
    },
    "athlete": {
      "type": "object",
      "required": [
        "display_name",
        "age_band",
        "experience_level"
      ],
      "properties": {
        "display_name": {
          "type": "string",
          "minLength": 1
        },
        "age_band": {
          "enum": [
            "18-29",
            "30-39",
            "40-49",
            "50-59",
            "60-69",
            "70+"
          ]
        },
        "experience_level": {
          "enum": [
            "novice",
            "intermediate",
            "experienced",
            "competitive"
          ]
        },
        "sex_for_optional_reference_equations": {
          "enum": [
            "female",
            "male",
            "not_provided"
          ]
        },
        "boat_classes": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "medical_review_required": {
          "type": "boolean",
          "default": false
        },
        "coach_review_requested": {
          "type": "boolean",
          "default": false
        }
      },
      "additionalProperties": false
    },
    "season": {
      "type": "object",
      "required": [
        "start_date",
        "end_date",
        "current_weekly_endurance_minutes",
        "target_peak_weekly_endurance_minutes"
      ],
      "properties": {
        "start_date": {
          "type": "string",
          "format": "date"
        },
        "end_date": {
          "type": "string",
          "format": "date"
        },
        "current_weekly_endurance_minutes": {
          "type": "integer",
          "minimum": 0
        },
        "target_peak_weekly_endurance_minutes": {
          "type": "integer",
          "minimum": 0
        },
        "default_block_pattern": {
          "enum": [
            "3_build_1_deload",
            "2_build_1_deload",
            "custom"
          ]
        }
      },
      "additionalProperties": false
    },
    "goals": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": [
          "goal_type",
          "priority"
        ],
        "properties": {
          "goal_type": {
            "enum": [
              "head_5k",
              "sprint_1k",
              "erg_2k",
              "technique",
              "general_fitness",
              "other"
            ]
          },
          "priority": {
            "enum": [
              "primary",
              "secondary"
            ]
          },
          "target_rate_low": {
            "type": [
              "integer",
              "null"
            ],
            "minimum": 12,
            "maximum": 50
          },
          "target_rate_high": {
            "type": [
              "integer",
              "null"
            ],
            "minimum": 12,
            "maximum": 50
          },
          "notes": {
            "type": "string"
          }
        },
        "additionalProperties": false
      }
    },
    "tests": {
      "type": "object",
      "properties": {
        "test_date": {
          "type": [
            "string",
            "null"
          ],
          "format": "date"
        },
        "resting_hr": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 30,
          "maximum": 110
        },
        "max_hr": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 100,
          "maximum": 230
        },
        "erg_2k_seconds": {
          "type": [
            "number",
            "null"
          ],
          "minimum": 300,
          "maximum": 1200
        },
        "lt1_hr": {
          "type": [
            "integer",
            "null"
          ]
        },
        "lt2_hr": {
          "type": [
            "integer",
            "null"
          ]
        },
        "lt1_watts": {
          "type": [
            "number",
            "null"
          ]
        },
        "lt2_watts": {
          "type": [
            "number",
            "null"
          ]
        },
        "seven_stroke_peak_watts": {
          "type": [
            "number",
            "null"
          ],
          "minimum": 1,
          "deprecated": true,
          "description": "Legacy v0.1 field. Migrate to multi_duration_power_tests."
        },
        "sixty_second_avg_watts": {
          "type": [
            "number",
            "null"
          ],
          "minimum": 1,
          "deprecated": true,
          "description": "Legacy v0.1 field. Migrate to multi_duration_power_tests."
        },
        "thirty_minute_r20_avg_watts": {
          "type": [
            "number",
            "null"
          ],
          "minimum": 1,
          "deprecated": true,
          "description": "Legacy v0.1 field. Migrate to multi_duration_power_tests."
        },
        "coach_defined_bands": {
          "type": [
            "array",
            "null"
          ],
          "items": {
            "$ref": "intensity_band.schema.json"
          }
        },
        "notes": {
          "type": "string"
        },
        "multi_duration_power_tests": {
          "type": [
            "object",
            "null"
          ],
          "properties": {
            "battery_id": {
              "type": [
                "string",
                "null"
              ]
            },
            "short_peak": {
              "type": [
                "object",
                "null"
              ],
              "properties": {
                "value_watts": {
                  "type": "number",
                  "exclusiveMinimum": 0
                },
                "protocol": {
                  "enum": [
                    "seven_stroke",
                    "ten_second",
                    "other_short_peak"
                  ]
                },
                "duration_seconds": {
                  "type": [
                    "number",
                    "null"
                  ],
                  "exclusiveMinimum": 0
                },
                "test_date": {
                  "type": [
                    "string",
                    "null"
                  ],
                  "format": "date"
                },
                "erg_model": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "drag_factor": {
                  "type": [
                    "integer",
                    "null"
                  ],
                  "minimum": 50,
                  "maximum": 250
                },
                "validity": {
                  "enum": [
                    "valid",
                    "questionable",
                    "invalid",
                    "unknown"
                  ]
                },
                "notes": {
                  "type": "string"
                }
              },
              "required": [
                "value_watts",
                "protocol",
                "validity"
              ],
              "additionalProperties": false
            },
            "one_minute": {
              "type": [
                "object",
                "null"
              ],
              "properties": {
                "value_watts": {
                  "type": "number",
                  "exclusiveMinimum": 0
                },
                "duration_seconds": {
                  "const": 60
                },
                "average_rate_spm": {
                  "type": [
                    "number",
                    "null"
                  ],
                  "minimum": 10,
                  "maximum": 60
                },
                "peak_rate_spm": {
                  "type": [
                    "number",
                    "null"
                  ],
                  "minimum": 10,
                  "maximum": 70
                },
                "test_date": {
                  "type": [
                    "string",
                    "null"
                  ],
                  "format": "date"
                },
                "erg_model": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "drag_factor": {
                  "type": [
                    "integer",
                    "null"
                  ],
                  "minimum": 50,
                  "maximum": 250
                },
                "validity": {
                  "enum": [
                    "valid",
                    "questionable",
                    "invalid",
                    "unknown"
                  ]
                },
                "notes": {
                  "type": "string"
                }
              },
              "required": [
                "value_watts",
                "duration_seconds",
                "validity"
              ],
              "additionalProperties": false
            },
            "rate_capped_sustained": {
              "type": [
                "object",
                "null"
              ],
              "properties": {
                "value_watts": {
                  "type": "number",
                  "exclusiveMinimum": 0
                },
                "duration_seconds": {
                  "type": "integer",
                  "minimum": 300,
                  "maximum": 7200
                },
                "rate_cap_spm": {
                  "type": "integer",
                  "minimum": 14,
                  "maximum": 30
                },
                "average_hr": {
                  "type": [
                    "integer",
                    "null"
                  ],
                  "minimum": 50,
                  "maximum": 230
                },
                "max_hr": {
                  "type": [
                    "integer",
                    "null"
                  ],
                  "minimum": 50,
                  "maximum": 230
                },
                "test_date": {
                  "type": [
                    "string",
                    "null"
                  ],
                  "format": "date"
                },
                "erg_model": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "drag_factor": {
                  "type": [
                    "integer",
                    "null"
                  ],
                  "minimum": 50,
                  "maximum": 250
                },
                "validity": {
                  "enum": [
                    "valid",
                    "questionable",
                    "invalid",
                    "unknown"
                  ]
                },
                "notes": {
                  "type": "string"
                }
              },
              "required": [
                "value_watts",
                "duration_seconds",
                "rate_cap_spm",
                "validity"
              ],
              "additionalProperties": false
            },
            "previous_batteries": {
              "type": "array",
              "items": {
                "type": "object"
              }
            }
          },
          "additionalProperties": false
        },
        "power_profile_settings": {
          "type": [
            "object",
            "null"
          ],
          "properties": {
            "mode": {
              "enum": [
                "anchors_only",
                "coach_defined_reference",
                "research_pilot"
              ]
            },
            "allow_provisional_low_intensity_watts_from_sustained_test": {
              "type": "boolean"
            },
            "reference_set_id": {
              "type": [
                "string",
                "null"
              ]
            },
            "coach_reviewed": {
              "type": "boolean"
            }
          },
          "required": [
            "mode",
            "allow_provisional_low_intensity_watts_from_sustained_test",
            "coach_reviewed"
          ],
          "additionalProperties": false
        }
      },
      "additionalProperties": false
    },
    "weekly_availability": {
      "type": "array",
      "minItems": 7,
      "maxItems": 7,
      "items": {
        "type": "object",
        "required": [
          "weekday",
          "available",
          "fixed_rest",
          "max_training_minutes",
          "max_sessions"
        ],
        "properties": {
          "weekday": {
            "enum": [
              "monday",
              "tuesday",
              "wednesday",
              "thursday",
              "friday",
              "saturday",
              "sunday"
            ]
          },
          "available": {
            "type": "boolean"
          },
          "fixed_rest": {
            "type": "boolean"
          },
          "max_training_minutes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 300
          },
          "max_sessions": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3
          },
          "heavy_lifting": {
            "type": "boolean"
          },
          "lifting_minutes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 180
          },
          "alternate_ut2_allowed": {
            "type": "boolean"
          },
          "alternate_ut2_modes": {
            "type": "array",
            "items": {
              "enum": [
                "treadmill_walk_jog",
                "elliptical",
                "bike",
                "other"
              ]
            }
          },
          "row_on_lifting_day": {
            "type": "boolean"
          },
          "fixed_coached_row": {
            "type": "boolean"
          },
          "expected_coached_intensity": {
            "enum": [
              "unknown",
              "technique",
              "ut2",
              "ut2_ut1",
              "quality"
            ]
          },
          "rowing_modes": {
            "type": "array",
            "items": {
              "enum": [
                "on_water",
                "erg"
              ]
            }
          },
          "notes": {
            "type": "string"
          }
        },
        "additionalProperties": false
      }
    },
    "races": {
      "type": "array",
      "items": {
        "$ref": "race.schema.json"
      }
    },
    "preferences": {
      "type": "object",
      "required": [
        "terminology",
        "erg_primary_display",
        "broken_aerobic_preferred"
      ],
      "properties": {
        "terminology": {
          "enum": [
            "UT",
            "three_zone",
            "custom"
          ]
        },
        "erg_primary_display": {
          "enum": [
            "watts",
            "split",
            "both"
          ]
        },
        "broken_aerobic_preferred": {
          "type": "boolean"
        },
        "fixed_rest_weekdays": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "workbook_detail_level": {
          "enum": [
            "concise",
            "standard",
            "detailed"
          ]
        },
        "include_sources_sheet": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "locked_weeks": {
      "type": "array",
      "items": {
        "type": "string",
        "format": "date"
      }
    }
  },
  "additionalProperties": false
}
```

---

# GENERATED PLAN SCHEMA

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "generated_plan.schema.json",
  "title": "GeneratedRowingPlan",
  "type": "object",
  "required": [
    "plan_version",
    "profile_id",
    "generated_at",
    "intensity_profile",
    "power_profile",
    "phases",
    "sessions",
    "warnings"
  ],
  "properties": {
    "plan_version": {
      "type": "string"
    },
    "profile_id": {
      "type": "string"
    },
    "generated_at": {
      "type": "string",
      "format": "date-time"
    },
    "intensity_profile": {
      "type": "array",
      "items": {
        "$ref": "intensity_band.schema.json"
      }
    },
    "phases": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "sessions": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "weekly_totals": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "object"
      }
    },
    "source_ids": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "power_profile": {
      "oneOf": [
        {
          "$ref": "power_profile.schema.json"
        },
        {
          "type": "null"
        }
      ]
    },
    "algorithm_versions": {
      "type": "object",
      "properties": {
        "planner": {
          "type": "string"
        },
        "power_profile": {
          "type": [
            "string",
            "null"
          ]
        },
        "config": {
          "type": "string"
        }
      },
      "additionalProperties": false
    },
    "change_summary": {
      "type": "array",
      "items": {
        "type": "object"
      }
    }
  },
  "additionalProperties": false
}
```

---

# SAMPLE ATHLETE

```json
{
  "profile_version": "0.2",
  "athlete": {
    "display_name": "Sample masters rower",
    "age_band": "50-59",
    "experience_level": "competitive",
    "sex_for_optional_reference_equations": "female",
    "boat_classes": [
      "single",
      "double"
    ],
    "medical_review_required": false,
    "coach_review_requested": false
  },
  "season": {
    "start_date": "2026-08-05",
    "end_date": "2026-11-15",
    "current_weekly_endurance_minutes": 260,
    "target_peak_weekly_endurance_minutes": 330,
    "default_block_pattern": "3_build_1_deload"
  },
  "goals": [
    {
      "goal_type": "head_5k",
      "priority": "primary",
      "target_rate_low": 30,
      "target_rate_high": 32,
      "notes": "Primary fall goal."
    },
    {
      "goal_type": "sprint_1k",
      "priority": "secondary",
      "target_rate_low": 34,
      "target_rate_high": 35,
      "notes": "Future sprint goal; maintain limited speed exposure."
    }
  ],
  "tests": {
    "test_date": "2026-07-30",
    "resting_hr": 58,
    "max_hr": 177,
    "erg_2k_seconds": null,
    "lt1_hr": null,
    "lt2_hr": null,
    "lt1_watts": null,
    "lt2_watts": null,
    "seven_stroke_peak_watts": 287,
    "sixty_second_avg_watts": 220,
    "thirty_minute_r20_avg_watts": 143.8,
    "coach_defined_bands": null,
    "notes": "No current 2k. Use HRR/RPE for low-intensity bands. Use the independent multi-duration profile for active PP/AN anchors and sustained-power plausibility checks; do not predict 2k.",
    "multi_duration_power_tests": {
      "battery_id": "sample-2026-07",
      "short_peak": {
        "value_watts": 287,
        "protocol": "seven_stroke",
        "duration_seconds": null,
        "test_date": "2026-07-30",
        "erg_model": "Concept2 RowErg",
        "drag_factor": null,
        "validity": "valid",
        "notes": "Athlete-reported best power."
      },
      "one_minute": {
        "value_watts": 220,
        "duration_seconds": 60,
        "average_rate_spm": null,
        "peak_rate_spm": null,
        "test_date": "2026-07-30",
        "erg_model": "Concept2 RowErg",
        "drag_factor": null,
        "validity": "valid",
        "notes": "Athlete-reported average power."
      },
      "rate_capped_sustained": {
        "value_watts": 143.8,
        "duration_seconds": 1800,
        "rate_cap_spm": 20,
        "average_hr": null,
        "max_hr": null,
        "test_date": "2026-07-30",
        "erg_model": "Concept2 RowErg",
        "drag_factor": null,
        "validity": "valid",
        "notes": "Thirty-minute average at a 20 spm cap."
      },
      "previous_batteries": []
    },
    "power_profile_settings": {
      "mode": "anchors_only",
      "allow_provisional_low_intensity_watts_from_sustained_test": false,
      "reference_set_id": null,
      "coach_reviewed": false
    }
  },
  "weekly_availability": [
    {
      "weekday": "monday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 110,
      "max_sessions": 2,
      "heavy_lifting": true,
      "lifting_minutes": 60,
      "alternate_ut2_allowed": true,
      "alternate_ut2_modes": [
        "treadmill_walk_jog",
        "elliptical"
      ],
      "row_on_lifting_day": false,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [
        "on_water",
        "erg"
      ],
      "notes": "Heavy lift then alternate UT2; cannot row."
    },
    {
      "weekday": "tuesday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 90,
      "max_sessions": 1,
      "heavy_lifting": false,
      "lifting_minutes": 0,
      "alternate_ut2_allowed": false,
      "alternate_ut2_modes": [],
      "row_on_lifting_day": true,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [
        "on_water",
        "erg"
      ],
      "notes": "Primary quality row."
    },
    {
      "weekday": "wednesday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 90,
      "max_sessions": 1,
      "heavy_lifting": false,
      "lifting_minutes": 0,
      "alternate_ut2_allowed": false,
      "alternate_ut2_modes": [],
      "row_on_lifting_day": true,
      "fixed_coached_row": true,
      "expected_coached_intensity": "ut2_ut1",
      "rowing_modes": [
        "on_water"
      ],
      "notes": "Private lesson: technique then UT2/UT1 pieces."
    },
    {
      "weekday": "thursday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 70,
      "max_sessions": 1,
      "heavy_lifting": false,
      "lifting_minutes": 0,
      "alternate_ut2_allowed": false,
      "alternate_ut2_modes": [],
      "row_on_lifting_day": true,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [
        "on_water",
        "erg"
      ],
      "notes": "Optional/easy row; first session to drop."
    },
    {
      "weekday": "friday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 110,
      "max_sessions": 2,
      "heavy_lifting": true,
      "lifting_minutes": 60,
      "alternate_ut2_allowed": true,
      "alternate_ut2_modes": [
        "treadmill_walk_jog",
        "elliptical"
      ],
      "row_on_lifting_day": false,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [
        "on_water",
        "erg"
      ],
      "notes": "Heavy lift then alternate UT2; cannot row."
    },
    {
      "weekday": "saturday",
      "available": false,
      "fixed_rest": true,
      "max_training_minutes": 0,
      "max_sessions": 0,
      "heavy_lifting": false,
      "lifting_minutes": 0,
      "alternate_ut2_allowed": false,
      "alternate_ut2_modes": [],
      "row_on_lifting_day": false,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [],
      "notes": "Fixed rest day."
    },
    {
      "weekday": "sunday",
      "available": true,
      "fixed_rest": false,
      "max_training_minutes": 110,
      "max_sessions": 1,
      "heavy_lifting": false,
      "lifting_minutes": 0,
      "alternate_ut2_allowed": false,
      "alternate_ut2_modes": [],
      "row_on_lifting_day": true,
      "fixed_coached_row": false,
      "expected_coached_intensity": "unknown",
      "rowing_modes": [
        "on_water",
        "erg"
      ],
      "notes": "Longer predominantly UT2 row; avoid routine maximal work before Monday lift."
    }
  ],
  "races": [
    {
      "event_name": "September benchmark head race",
      "start_date": "2026-09-26",
      "end_date": "2026-09-26",
      "race_type": "head_5k",
      "distance_m": 5000,
      "priority": "C",
      "boat_class": "single",
      "expected_races": 1,
      "travel_days_before": 0,
      "travel_days_after": 0,
      "benchmark_for_future_plan": true,
      "notes": "Minimal taper."
    },
    {
      "event_name": "October rehearsal head race",
      "start_date": "2026-10-17",
      "end_date": "2026-10-17",
      "race_type": "head_5k",
      "distance_m": 5000,
      "priority": "B",
      "boat_class": "single",
      "expected_races": 1,
      "travel_days_before": 0,
      "travel_days_after": 0,
      "benchmark_for_future_plan": true,
      "notes": "Moderate taper and pacing rehearsal."
    },
    {
      "event_name": "Primary multi-race head weekend",
      "start_date": "2026-11-07",
      "end_date": "2026-11-08",
      "race_type": "head_5k",
      "distance_m": 5000,
      "priority": "A",
      "boat_class": "multiple",
      "expected_races": 3,
      "travel_days_before": 0,
      "travel_days_after": 0,
      "benchmark_for_future_plan": true,
      "notes": "Primary peak; two to three races."
    }
  ],
  "preferences": {
    "terminology": "UT",
    "erg_primary_display": "both",
    "broken_aerobic_preferred": true,
    "fixed_rest_weekdays": [
      "saturday"
    ],
    "workbook_detail_level": "detailed",
    "include_sources_sheet": true
  },
  "locked_weeks": []
}
```

---

# SOURCE REGISTER

```csv
source_id,author_or_org,title,year,doi,url,source_type,license_or_access,evidence_area,allowed_use,caution,date_checked
S001,U.S. Copyright Office,What is Copyright?,Official government guidance,n/a,https://www.copyright.gov/what-is-copyright/,Government website,Publicly accessible,Copyright boundary,Use principle that copyright protects expression rather than ideas/methods; paraphrase.,Do not treat this as legal advice.,2026-08-06
S002,U.S. Copyright Office,"17 U.S.C. §102(b), Circular 92 Chapter 1",Statute,n/a,https://www.copyright.gov/title17/92chap1.html,Government statute,Publicly accessible,Copyright boundary,Use statutory distinction for product policy.,Legal review still required.,2026-08-06
S003,Creative Commons,About CC Licenses,Official license guidance,n/a,https://creativecommons.org/share-your-work/cclicenses/,License guidance,Varies by license,Licensing,Use to interpret attribution/adaptation requirements.,Verify the exact license on each reused work.,2026-08-06
S004,Creative Commons,CC0 and Public Domain tools,Official license guidance,n/a,https://creativecommons.org/public-domain/,License guidance,CC0/PDM information,Licensing,Prefer CC0/public-domain assets where practical.,Only rights holders can apply CC0 to their own work.,2026-08-06
S005,Concept2,Pace and Watts Calculators,Official technical documentation,n/a,https://www.concept2.com/training/watts-calculator,Manufacturer documentation,Publicly accessible,Erg conversion,Implement formula and cite Concept2.,Do not imply Concept2 endorsement.,2026-08-06
S006,Concept2,PM5 usage and units,Official technical documentation,n/a,https://www.concept2.com/support/monitors/pm5/how-to-use,Manufacturer documentation,Publicly accessible,Erg display,Explain that pace and watts are alternative PM units.,Do not copy page prose.,2026-08-06
S007,Streamlit,st.download_button,Official software documentation,n/a,https://docs.streamlit.io/develop/api-reference/widgets/st.download_button,Software documentation,Publicly accessible,MVP implementation,Use download button for generated workbook.,Check API during implementation.,2026-08-06
S008,FastAPI,Features and data validation,Official software documentation,n/a,https://fastapi.tiangolo.com/features/,Software documentation,Publicly accessible,Future architecture,Use typed validation and OpenAPI in later version.,Not required for first Streamlit build.,2026-08-06
S009,Wang et al.,Effects of tapering on performance in endurance athletes,2023,10.1371/journal.pone.0282838,https://pubmed.ncbi.nlm.nih.gov/37163550/,Systematic review/meta-analysis,Open access CC BY,Tapering,Use findings to set configurable taper ranges and preserve intensity.,Not rowing-specific; individualize.,2026-08-06
S010,Zhong et al.,"Training-Intensity Distribution, Volume, Periodization, and Performance in Elite Rowers",2025,10.1123/ijspp.2024-0433,https://pubmed.ncbi.nlm.nih.gov/40185480/,Rowing systematic review,Free article; verify reuse license,Periodization/TID,Use findings that rowing TID varies seasonally and no single model is clearly superior.,Elite sample and small evidence base; do not copy tables.,2026-08-06
S011,Treff et al.,Eleven-Week Preparation Involving Polarized Intensity Distribution Is Not Superior to Pyramidal Distribution in National Elite Rowers,2017,n/a,https://pubmed.ncbi.nlm.nih.gov/28824440/,Controlled rowing study,Abstract/metadata use,TID,Support configurable rather than dogmatic TID.,Male national-elite sample.,2026-08-06
S012,Ingham et al.,"Determinants of 2,000 m rowing ergometer performance in elite rowers",2002,n/a,https://pubmed.ncbi.nlm.nih.gov/12458367/,Rowing physiology study,Abstract/metadata use,Testing/profile,Support 2k as integrated performance test and importance of peak/aerobic factors.,Elite sample; do not reproduce regression model without reviewing full rights and methods.,2026-08-06
S013,Systematic review authors,The Evaluation of Physical Performance in Rowing Ergometer,2025,n/a,https://pubmed.ncbi.nlm.nih.gov/41283544/,Systematic review,Abstract/metadata use,Testing,"Support 2k, incremental tests, peak power, and critical power as common assessment approaches.",Female evidence underrepresented.,2026-08-06
S014,Driller et al.,The effects of high-intensity interval training in well-trained rowers,2009,n/a,https://pubmed.ncbi.nlm.nih.gov/19417232/,Rowing intervention study,Abstract/metadata use,HIIT,Use as evidence that aerobic-power intervals can improve rowing performance.,Do not copy protocol wording; sample is well-trained.,2026-08-06
S015,Ní Chéilleachair et al.,HIIT enhances endurance performance and aerobic characteristics more than high-volume training in trained rowers,2016,n/a,https://pubmed.ncbi.nlm.nih.gov/27438378/,Rowing intervention study,Abstract/metadata use,HIIT,Support limited high-intensity sessions within mostly aerobic training.,Protocol-specific; do not universalize.,2026-08-06
S016,Akca and Aras,Comparison of Rowing Performance Improvements Following Various High-Intensity Interval Trainings,2015,10.1519/JSC.0000000000000870,https://pubmed.ncbi.nlm.nih.gov/25647654/,Rowing intervention study,Abstract/metadata use,HIIT/anaerobic,Support short and longer high-intensity interval categories.,"Male collegiate sample; exact protocols remain cited research, not copied app plan.",2026-08-06
S017,Huiberts et al.,Concurrent Strength and Endurance Training: Impact of Sex and Training Status,2024,10.1007/s40279-023-01943-9,https://pubmed.ncbi.nlm.nih.gov/37847373/,Systematic review/meta-analysis,Open access,Concurrent training,Support combined strength and endurance planning and caution about population differences.,Evidence does not determine an exact weekly schedule.,2026-08-06
S018,Tran et al.,Convergent validity of a novel method for quantifying rowing training loads,2015,10.1080/02640414.2014.942686,https://pubmed.ncbi.nlm.nih.gov/25083912/,Rowing monitoring study,Abstract/metadata use,Time in zone,Support actual time-in-zone and mode-specific accounting.,"Elite sample; app uses simplified accounting, not the proprietary T2minute method.",2026-08-06
S019,High-level rower study authors,Increases in RPE Rating Predict Fatigue Accumulation Without Changes in Heart Rate Zone Distribution,2021,n/a,https://pubmed.ncbi.nlm.nih.gov/34603086/,Rowing monitoring study,Abstract/metadata use,Monitoring,Support recording both HR and perceived effort.,Small high-level sample.,2026-08-06
S020,Garland and Atkinson,Effect of blood lactate sample site and test protocol on training zone prescription in rowing,2008,10.1123/ijspp.3.3.347,https://pubmed.ncbi.nlm.nih.gov/19211946/,Rowing testing study,Abstract/metadata use,Threshold testing,Support labeling test-protocol-specific boundaries and avoiding false precision.,Does not provide a universal zone table.,2026-08-06
S021,Cerasola et al.,Can the 20 and 60 s All-Out Test Predict the 2000 m Indoor Rowing Performance in Athletes?,2022,10.3389/fphys.2022.828710,https://pubmed.ncbi.nlm.nih.gov/35721540/,Primary rowing study; open-access article,Open access; verify article license for reuse,Short-duration rowing power profile,Support relevance of short and 60-second power measurements and descriptive within-athlete ratios.,Youth male rowers; 20-second test is not a seven-stroke peak test; do not copy regression equation or generalize prediction.,2026-08-06
S022,Cerasola et al.,"Predicting the 2000-m Rowing Ergometer Performance from Anthropometric, Maximal Oxygen Uptake and 60-s Mean Power Variables in National Level Young Rowers",2020,n/a,https://pubmed.ncbi.nlm.nih.gov/33312296/,Primary rowing study,Abstract/metadata use; verify full-text license,One-minute power,Support use of 60-second average power as a meaningful rowing performance measurement.,Small national-level youth male sample; do not use its regression model for general adult or masters prediction.,2026-08-06
S023,Rappelt et al.,Performance Prediction and Athlete Categorization using the Anaerobic Power Reserve Framework in Rowing,2026,n/a,https://pubmed.ncbi.nlm.nih.gov/41348913/,Primary rowing study,Abstract/metadata use; verify full-text license,Power-reserve profiling,Support the general relevance of combining peak and aerobic/mechanical power measures for athlete profiling.,Sub-elite/elite female and male rowers; uses different tests and framework; do not claim validation of this app algorithm.,2026-08-06
S024,U.S. Copyright Office,37 CFR § 202.1 — Material not subject to copyright,Current through 2026-08-03,n/a,https://www.copyright.gov/title37/202/37cfr202-1.html,Government regulation guidance,Publicly accessible,Copyright boundary,Support distinction between methods/systems and their written expression.,"Does not resolve contracts, trademarks, patents, trade secrets, or fact-specific infringement questions.",2026-08-06
S025,U.S. Copyright Office,Copyright in General FAQ,Current,n/a,https://www.copyright.gov/help/faq/faq-general.html,Government guidance,Publicly accessible,Automatic protection and notice,Support that protection arises on fixation and notice/registration are not required for existence of copyright.,Product policy is not legal advice.,2026-08-06

```
