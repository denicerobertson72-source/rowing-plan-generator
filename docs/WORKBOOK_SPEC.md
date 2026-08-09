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
