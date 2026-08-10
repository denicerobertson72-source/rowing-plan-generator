# PWA Milestone 3

The PWA now supports a daily athlete experience backed by the generated plan.

- Today loads current sessions from the API and falls back to the next planned session when today is empty.
- Week displays seven days, including protected rest days, strength/alternate UT2, coached sessions, race days, and rowing sessions from the plan.
- Workout detail uses a structured API display model that distinguishes erg, on-water, coached lesson, strength, alternate UT2, race, and cross-training states.
- The PWA saves the latest generated plan in browser storage and shows an explicit offline/stale message if API access fails.

The service worker caches GET responses; full offline completion-log queueing is deliberately deferred to the logging milestone. Workout logging is not yet exposed as an athlete-facing control, although the API persistence boundary exists.
