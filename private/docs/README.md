# Private ops docs

- Put real secrets in environment variables or a local `.env` (gitignored).
- See `secrets.example` for variable names.
- For Railway: set `PROPORACLE_DB_PATH` only if you mount a persistent volume; otherwise use hosted Postgres and point `DATABASE_URL` after wiring the SQL schema.
