# Step 6B UX behavior

Race creation and editing use a local modal draft. The persisted chronological list is never modified while the athlete types. A confirmed save writes the changed race, refreshes profile state, closes the modal, and then displays the sorted persisted list. Failed saves leave the modal and draft unchanged.

`SaveFeedbackProvider` is mounted once in the root layout. Its success and error notices are accessible live regions, success notices dismiss automatically, errors can be dismissed, and duplicate submissions are guarded by a saving state.

Profile, race, schedule, tests, plan refresh, and workout-log saves show feedback only after their request succeeds. Profile-derived saves explain that the plan needs an update; regeneration says “Plan updated” only after generation completes.

The existing rowing icon remains a single low-opacity, pointer-inert fixed background watermark from the shared stylesheet. Modal cards remain opaque, and the existing mobile rules turn modal cards into bottom sheets below 430px.

Browser acceptance uses `services/api/tests/disposable_browser_runner.py`. It injects the temporary dependency directory into the Uvicorn process `PYTHONPATH`, requires `ROWING_PLAN_DB_PATH` to resolve outside the repository development data directory, and terminates Uvicorn before the fixture deletes its database.
