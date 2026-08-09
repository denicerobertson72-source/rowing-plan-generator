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
