# Disposable database integration stack

This stack runs PostgreSQL, GoTrue, and PostgREST for local contract tests. It
uses real password sign-in tokens and real row-level security. It does not
connect to a hosted Supabase project or use production accounts, data, or keys.

The runner supplies a unique Compose project name and freshly generated,
URL-safe `INTEGRATION_DB_PASSWORD` and `INTEGRATION_JWT_SECRET` values. Both are
required; no credentials are read from an application `.env` file. Only the
proxy publishes a port, on `127.0.0.1` with a random host port. PostgreSQL data
lives in a container tmpfs. PostgreSQL, GoTrue, and PostgREST belong only to an
internal network with no external egress. The fixed-upstream proxy also joins
an ingress bridge so Docker can publish its localhost port.
The runner must tear down only its own Compose project on success or failure.

## Running the tests

Install the backend's development dependencies and start a local Docker engine
with Compose v2. The runner accepts only a local Unix-socket Docker endpoint;
it does not accept a remote database or Supabase URL. On its first run Docker
may need to download the four fixture images.

From the APIservice repository root, use the sibling main-site checkout:

```sh
RUN_MAIN_SITE_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_database.py -q
```

This reads `../gtesports-website/supabase` by default. For a pinned checkout,
set both its schema directory and the full expected commit SHA:

```sh
MAIN_SITE_SCHEMA_DIR=/absolute/path/to/pinned-main-site/supabase \
MAIN_SITE_SCHEMA_COMMIT='<40-character-commit-sha>' \
RUN_MAIN_SITE_INTEGRATION=1 \
.venv/bin/python -m pytest tests/integration/test_database.py -q
```

CI checks out its fixed frontend commit into `.integration-main-site`, sets
`MAIN_SITE_SCHEMA_DIR` to that checkout's `supabase` directory, and passes the
same SHA as `MAIN_SITE_SCHEMA_COMMIT`. The runner rejects a different HEAD or
modified/missing SQL source before starting any containers. Without
`RUN_MAIN_SITE_INTEGRATION=1`, ordinary pytest runs skip all nine checks and do
not access Docker. No application `.env`, hosted credentials, or fixed test
passwords are needed.

## Services and fixture boundaries

- `database`: PostgreSQL 17; database/user `postgres`, no published port. The
  runner sends migration SQL through `docker compose exec -T database psql`.
- `auth`: GoTrue `v2.197.0`; creates its own `auth` tables and migrations.
  Its database connection explicitly uses `search_path=auth`.
  Password sign-up is automatically confirmed for these disposable test users.
  SMTP, Discord OAuth, email delivery, and other external providers are not
  exercised. Its configured issuer uses a fixed local fixture URL; the actual
  proxy host port is discovered by the runner.
- `rest`: PostgREST `v14.18`; connects as a non-superuser `authenticator` that
  can assume `anon` or `authenticated`. Its two-row cap deliberately exercises
  pagination. User JWTs have the `authenticated` audience and are checked by
  PostgREST before the database evaluates the caller's RLS policies.
- `proxy`: nginx `1.28-alpine`; forwards `/auth/v1/` and `/rest/v1/`. The
  `/health` response checks only the proxy. The runner separately waits for
  `/auth/v1/health` and `/rest/v1/` before applying application migrations.

The proxy intentionally does not implement the hosted Supabase API-key gateway.
The local `sb_publishable_fixture` value allows APIservice's configuration path
to run, but this fixture does not establish that Kong/Envoy API-key enforcement,
hosted publishable-key behavior, or production TLS is correct. Authorization
inside GoTrue, JWT validation inside PostgREST, and database RLS are real.
GoTrue connects as `postgres` only in this isolated test fixture; APIservice
never receives that database password or a service-role API key.

## Application schema

Application migrations and SQL checks come from a pinned commit of
`gt-esports/gtesports-website`; they are not duplicated here. Apply them only
after GoTrue is healthy so that `auth.users` exists, then notify PostgREST to
reload its schema cache. The integration runner records each SQL file's SHA256.
Set `MAIN_SITE_SCHEMA_COMMIT` to the expected full Git commit SHA to additionally
verify the source checkout and require every loaded SQL file to match that
commit. CI sets this to its pinned checkout SHA. Local development can omit it
to exercise uncommitted migration changes; the recorded file hashes still
identify the exact inputs.

`bootstrap.sql` creates the Supabase-style database roles and `auth.uid()`
support for both the legacy single-claim setting used by existing SQL checks
and the JSON claims set by PostgREST. It intentionally adds **no default table
grants**. Contacts and roles obtain privileges from their tracked migrations;
authenticated profile reads must also come from the tracked frontend grant
migration. This catches missing grants rather than hiding them behind an older
Supabase default-privilege configuration.

Passing this stack proves local behavior against these service versions and
the pinned schema. Hosted deployment, real accounts, Discord sign-in, API-key
gateway policy, and production configuration still require separate staging
verification.
