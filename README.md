# Esports API Platform

The first integrated feature is the public GameFest Hall of Fame. The gateway
serves `GET /v1/gamefest/past-winners` from the existing GameFest Supabase project,
using its anonymous public read permissions. It does not write to the database,
change its schema, or replace existing website authentication.

The main-site integration also provides `GET /v1/me` and administrator-only
marketing contact APIs. Supabase continues to own accounts, sessions and data.
Each private request verifies the existing user token with Supabase Auth and
reads the current database role; contacts retain the caller's RLS permissions.
See [the main-site contract and rollout checks](docs/main-site-api.md).

## Run locally

Use Python 3.11 on macOS or Linux. From the repository root:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp infra/.env.example infra/.env
```

Set `GAMEFEST_SUPABASE_URL` and `GAMEFEST_SUPABASE_ANON_KEY` in `infra/.env`
to the same public Supabase project used by GameFest. The key must be an anonymous
JWT key or `sb_publishable_` key. Secret/service-role keys are rejected.
Keep `CORS_ALLOWED_ORIGINS=http://localhost:3000` for the local GameFest frontend.
HTTPS is required for remote Supabase hosts; HTTP is allowed only for loopback
addresses used in local development.

```sh
python -m uvicorn apps.gateway.main:app --reload --env-file infra/.env --host 127.0.0.1 --port 8000
```

- OpenAPI UI: <http://localhost:8000/docs>
- Liveness: `GET /health` (no external dependency)
- Readiness: `GET /ready` (checks the actual public data query, returns 503 on failure)
- Public results: `GET /v1/gamefest/past-winners`

```sh
curl -i http://localhost:8000/v1/gamefest/past-winners
python -m pytest -q
```

Tests use synthetic fixtures and an HTTP mock transport; no credentials or live
database are required. GitHub Actions runs the same suite for PRs and main.
The pinned requirements record the Python 3.11 environment used for this feature.

## Connect GameFest

The paired GameFest change replaces only `src/components/PastWinners.tsx` data
loading. In the GameFest checkout set:

```dotenv
VITE_ESPORTS_API_URL=http://localhost:8000
```

Keep GameFest's existing `VITE_SUPABASE_URL` and public key settings for its other
features. Start its Vite frontend on port 3000. The Hall of Fame calls APIservice
directly, without forwarding the user's Supabase session. There is no fallback
to a browser database query if this API fails.

For deployment, first deploy and verify APIservice, then build the GameFest
frontend with its HTTPS APIservice URL. Set APIservice's
`CORS_ALLOWED_ORIGINS` to the exact frontend origin(s), comma-separated, without
wildcards; include a preview origin when testing a preview deployment. Vite
variables are embedded at build time. GameFest's existing `/api/*` routes belong
to its Express/start.gg backend and are not an APIservice proxy.

See [the API contract and rollout checks](docs/gamefest-public-api.md).

## Container

After creating `infra/.env`, run:

```sh
docker compose -f infra/docker-compose.yml up --build
```

This runs the gateway and domain router in one container on `127.0.0.1:8000`.
The image runs as a non-root user and does not contain environment files.
The Docker recipe is provided for deployment preparation; CI validates the
Python application, not the container runtime.

## Development auth demo

The production gateway exposes no `/login` route and does not require a token
for public results. The earlier token demo is isolated in `apps.auth.main` and
disabled by default. To experiment locally, set `ENABLE_DEV_AUTH=true`, provide
a random `JWT_SECRET` of at least 32 characters, and use a separate loopback port:

```sh
python -m uvicorn apps.auth.main:app --env-file infra/.env --host 127.0.0.1 --port 8001
```

Its `/login` still accepts a development username and role without checking
identity. Never expose this demo publicly. JWT verification tests cover issuer,
audience, expiry, signature and required claims. This demo is independent of the
main-site integration, which verifies real sessions through Supabase Auth.

## Architecture direction

The [original architecture document](https://docs.google.com/document/d/1prQqau4Jkh9AYgPkPXGmu8ELvTry273Ot8y310UXGd0/edit)
proposes separate gateway, token issuer, domain services and Postgres schemas.
This milestone deliberately uses one deployable FastAPI app with domain routers
so one maintainer can deliver a working integration first. The GameFest domain
owns its repository, models and data transformation; the gateway owns request
IDs, JSON logging, errors and CORS. Separate process deployment, shared identity,
database consolidation and rate limiting remain future work.

Standalone GameFest/Hackathon `/hello` apps remain development stubs; they are
not proxies behind the gateway. No claims of production rollout are implied by
passing local tests. Use a feature branch and PR; the paired main-site and
GameFest repositories also require `@longxiangchen` review before merge.
