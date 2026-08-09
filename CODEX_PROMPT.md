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
