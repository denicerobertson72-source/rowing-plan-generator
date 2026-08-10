# Authentication and sync roadmap

Milestone 1 uses an in-memory development PlanRepository only. Production persistence will implement AthleteRepository, PlanRepository, TestRepository, WorkoutLogRepository, and RaceRepository with ownership via `user_id`. Authentication stays outside rowing-domain logic and is required before storing real athlete data.

Offline reads cache the latest Today/Week models in the browser. Offline completion-log writes are intentionally deferred: the UI/data contract will support them, while a future queue uses a local store and syncs when connectivity returns. No anonymous cross-user records may be exposed.
