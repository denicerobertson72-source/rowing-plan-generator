# Shared contracts

Existing JSON Schemas in `/schemas` remain the domain contracts. `workout_display.schema.json` defines the versioned, mobile-facing structured workout shape. The FastAPI OpenAPI document at `/api/v1/openapi.json` is the authoritative transport contract; TypeScript client generation replaces the Milestone 1 handwritten shim in Milestone 2.
