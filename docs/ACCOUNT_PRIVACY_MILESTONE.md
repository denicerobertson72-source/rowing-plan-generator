# Account and privacy milestone

The web client now sends the signed-in Supabase access token with every API request. The next server deployment step validates that token, assigns athlete records to its immutable Supabase user ID, and rejects cross-account reads and writes.

Before enabling sensitive tracking, configure the API project with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (not any secret key). Health tracking tables will be separate from athlete profile data, opt-in, exportable, and deletable. No symptom, menstrual, contraception, life-stage, or HRV value may automatically change training.
