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
