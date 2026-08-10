# PWA Milestone 1

Preserved engine modules: `conversions`, `intensity`, `power_profile`, `periodization`, `scheduler`, `validators`, and `workbook`. The Streamlit app is a temporary pilot UI and has not been removed.

API boundary: `POST /api/v1/plans/generate`, `GET /plans/{id}/today`, `GET /plans/{id}/week`, completion-log placeholder, and versioned OpenAPI. The browser must not calculate training zones or schedules.

Run locally:

```bash
python3 -m pip install -r services/api/requirements.txt
uvicorn services.api.app.main:app --reload
cd apps/web && npm install && npm run dev
```

Known limitations: persistence is in-memory, the PWA shell uses a demonstration Today card, the icons are vector placeholders, and offline cache currently covers shell/read responses only. Milestone 2 adds onboarding, persistence, and live plan generation.
