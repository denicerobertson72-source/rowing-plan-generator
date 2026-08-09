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
