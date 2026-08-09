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
