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

## Run the MVP

The initial local-first Streamlit MVP is in `app.py`. Create a virtual environment, install the listed dependencies, then start it:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

It opens with `data/sample_athlete.json`, supports JSON profile save/load, produces deterministic plans from the bundled original session library, and exports an app-owned Excel workbook. The implementation intentionally keeps power-profile anchors separate from intensity thresholds and never produces a predicted 2k.

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
