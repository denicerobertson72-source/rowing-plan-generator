# PWA Milestone 4

The PWA now supports quick, persistent workout completion logging and a practical season view.

- A rower can log a workout from Today or its detail page as completed, modified, or skipped.
- The form records actual duration, session RPE, optional average and peak heart rate, and free-form notes.
- Erg workouts also support average watts, split (in seconds), and stroke rate. On-water workouts support stroke rate, technical notes, and conditions. Coached sessions can be marked as changed by the coach.
- Logs are persisted in the local development SQLite repository through the versioned API.
- Season shows the current training phase, the next race with a day countdown, and phase transitions.

Current limitation: logs require a connection to save. Offline queueing and conflict reconciliation remain future sync work, as described in the auth and sync roadmap.
