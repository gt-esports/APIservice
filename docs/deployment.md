# APIservice deployment and read-only staging checks

The root `app.py` exports the same FastAPI application used by local Uvicorn
and the container. The deployment Python version is 3.12, selected by
`.python-version` and `infra/Dockerfile`. Public GameFest data and private
main-site data use separate Supabase configurations.

## Prepare the staging environment

1. Confirm the approved Vercel team and **APIservice** project, its connected
   repository, and its preview URL. This runbook does not create or relink a
   project. Keep the frontend projects connected to their own repositories.
2. Use a staging main-site Supabase project with the reviewed main-site
   migrations applied. The required objects include `profiles`, `user_roles`,
   `marketing_contacts`, current-role RLS, and the contact revision trigger.
   Include the paired main-site migration
   `20261026000100_grant_profile_reads.sql`: new projects need an explicit
   authenticated SELECT grant on `profiles`; the existing own-profile RLS
   policy still controls which row is visible. API startup never applies SQL.
3. Have an existing staging administrator and member account. Use Supabase's
   normal sign-in flow to obtain their current sessions. Do not issue a local
   development token or use a service-role credential for these checks.
4. Apply the main-site repository's database permission tests and the
   disposable integration checks before using a shared environment. Read-only
   smoke checks cannot prove insert/update/delete permissions or revision
   trigger behavior.

Configure these variables on the API project's **Preview** environment, and
independently on Production when the release is approved:

| Variable | Value |
| --- | --- |
| `MAIN_SITE_SUPABASE_URL` | Staging main site's HTTPS Supabase origin |
| `MAIN_SITE_SUPABASE_PUBLISHABLE_KEY` | That project's publishable or legacy anon key |
| `GAMEFEST_SUPABASE_URL` | GameFest's HTTPS Supabase origin |
| `GAMEFEST_SUPABASE_ANON_KEY` | GameFest's publishable or legacy anon key |
| `CORS_ALLOWED_ORIGINS` | Exact approved frontend origins, comma-separated |

For example, an approved production origin can be
`https://www.gatechesports.com`; add the exact frontend preview origin when
testing its preview. A Vercel wildcard is not supported by this application.
The actual preview origin must be recorded after the frontend deployment is
created. Loopback origins are only for local development. Leave
`ENABLE_DEV_AUTH` absent or false. No JWT signing secret is needed by the
production gateway. Never store access tokens or environment values in Git,
PR descriptions, screenshots, shell command arguments, or deployment logs.

## Vercel source deployment

Vercel's FastAPI preset detects `app.py` and the FastAPI dependency in
`requirements.txt`. It serves the application as one Python function. Use
the API repository root as the project Root Directory; leave Build Command
and Output Directory at the framework defaults. There is no route rewrite
or `/api` prefix: `/health`, `/v1/me`, and `/v1/main/ready` remain those paths.
The supported Python versions currently include 3.12; the committed pin
avoids depending on a future default. See the official
[FastAPI deployment guide](https://vercel.com/docs/frameworks/backend/fastapi)
and [Python runtime documentation](https://vercel.com/docs/functions/runtimes/python).

After the existing project and environment values have been checked, create
a **preview** from the feature branch through its connected Git integration
or the Vercel CLI. Review the build output for the selected Python version,
successful pinned dependency installation, and the exported ASGI application.
Inspect function logs for configuration failures. `.vercelignore` keeps local
environment files, virtual environments, tests, scripts, and Docker material
out of source uploads. The container is a separate runtime option and is not
the Vercel FastAPI entrypoint.

Existing deployment protection may intercept requests before FastAPI. Use
the team's approved preview-access mechanism when testing a protected
preview. A Vercel login page or redirect is not a successful API smoke check.
Do not disable deployment protection to make a failed check look successful.
Platform logs are separate from the application's sanitized JSON logs: keep
request bodies and authorization headers out of logging/export settings,
and restrict access to logs that include request URLs.

Vercel has a documented 4.5 MB request/response payload limit. The API's
contact JSON request limit is 4 MiB, and the browser CSV import limit is
1 MiB. Platform-level rejections can occur before the API's JSON error
handler; the frontend already treats unrecognized failures as unavailable.
See [function limits](https://vercel.com/docs/functions/limitations#request-body-size).
Keep the function duration above the API's three sequential, bounded
Supabase calls; the paired frontend uses a 20-second request timeout.

## Read-only smoke check

Install the repository's requirements in a local environment, then run:

```sh
python scripts/staging_smoke.py \
  --base-url https://your-approved-api-preview.vercel.app \
  --origin https://your-approved-frontend-preview.vercel.app
```

The tool prompts for a current administrator access token without echoing
it. For a protected CI secret environment, set `ESPORTS_SMOKE_ACCESS_TOKEN`,
`ESPORTS_API_URL`, and `ESPORTS_FRONTEND_ORIGIN` and run the script without
arguments. Alternatively, `--token-stdin` reads a token from a secure input
stream. There is intentionally no command-line token argument. Unset the
token variable after the check.

Only GET and OPTIONS requests are sent. The tool checks:

- `/health` and anonymous denial on `/v1/me`.
- Preflight methods and bearer headers from the exact frontend origin,
  plus denial of a randomly generated unconfigured origin.
- `/v1/me` with the administrator's current identity, profile, and role.
- `/v1/main/ready` and a single read-only contact page.
- `Cache-Control: no-store` on private responses and request IDs.

Output includes check names and sanitized failure summaries, never tokens,
contact values, upstream exception text, or response bodies. Exit status is
0 on success, 1 on a failed check, and 2 for invalid local input. The tool
does not follow redirects or retry requests. It does not change contacts,
roles, accounts, project settings, or deployments.

The health endpoints have different scopes:

| Endpoint | Meaning |
| --- | --- |
| `GET /health` | The application can respond; no external dependency check |
| `GET /ready` | GameFest's public data query succeeds |
| `GET /v1/main/ready` | An administrator's live Auth/role checks and a bounded contact schema/read probe succeed |

Main-site readiness returns only `{service: "main-site", status: "ready"}`.
It is protected by the same live administrator check and no-store middleware
as contacts. An empty contact table is valid. An empty RLS result cannot
distinguish an empty table from invisible rows, so readiness is not evidence
that every policy or write operation is correct. `/v1/me` in the smoke tool
separately verifies profile reads.

## Frontend rollout and rollback

After backend preview checks pass, build the paired main-site preview with
`VITE_ESPORTS_API_URL` set to that API origin. Preserve its Supabase settings;
Vite embeds the API URL during the build. Test sign-in, refresh, sign-out,
account switching, administrator/member restrictions, and the contact editor
in the browser. Use disposable records for separately authorized staging
write checks, including stale revision, duplicate imports, and opt-out
preservation. Record the frontend/backend commit IDs and deployment URLs.

Promote only the reviewed, verified releases. If the main-site release needs
rollback, restore its prior deployment; contacts still use the same Supabase
records. Roll back the API deployment separately if necessary. No automatic
merge, promotion, or database migration is performed by these repository
changes or the smoke tool.

## Container option

```sh
cp infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up --build
```

Fill in `infra/.env` before starting. The image runs Python 3.12 as a non-root
user, exposes Uvicorn on port 8000, and disables raw Uvicorn access logs in
favor of the gateway's sanitized JSON logs. Compose binds to
`127.0.0.1:8000`. The same smoke tool works with that loopback API origin and
an explicitly configured local frontend origin. Building or testing this
container does not validate Vercel's cloud build or production configuration.
