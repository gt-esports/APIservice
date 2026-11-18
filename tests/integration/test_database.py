"""Opt-in, disposable GoTrue/PostgREST/PostgreSQL integration checks."""

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from uuid import UUID, uuid4

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MAIN_SITE_INTEGRATION") != "1",
    reason="Set RUN_MAIN_SITE_INTEGRATION=1 to run the disposable Docker integration stack",
)

REPOSITORY = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).with_name("compose.yaml")
MIGRATIONS = (
    "00001_create_profiles.sql",
    "20261014012336_add_admin_authorization.sql",
    "20261014025403_add_marketing_contacts.sql",
    "20261014025827_add_marketing_contact_groups.sql",
    "20261026000100_grant_profile_reads.sql",
)
SQL_CHECKS = ("admin_authorization.sql", "marketing_contacts.sql")
CONTACT_PATH = "/v1/main/marketing-contacts"
PUBLIC_KEY = "sb_publishable_fixture"


def redact(value, sensitive):
    text = value.decode(errors="replace") if isinstance(value, bytes) else str(value or "")
    for secret in sensitive:
        if secret:
            text = text.replace(secret, "[fixture secret redacted]")
    return text[-6000:]


class Stack:
    def __init__(self):
        self.project = "gtesports-it-" + uuid4().hex
        self.database_password = secrets.token_urlsafe(32)
        self.jwt_secret = secrets.token_urlsafe(48)
        self.account_password = secrets.token_urlsafe(32)
        self.sensitive = [self.database_password, self.jwt_secret, self.account_password]
        self.environment = {
            **os.environ,
            "INTEGRATION_DB_PASSWORD": self.database_password,
            "INTEGRATION_JWT_SECRET": self.jwt_secret,
        }
        self.command = [
            "docker", "compose", "--env-file", os.devnull, "-f", str(COMPOSE_FILE),
            "--project-name", self.project,
        ]
        self.base_url = ""
        self.http = None
        self.api = None
        self.admin_id = ""
        self.member_id = ""
        self.admin_token = ""
        self.member_token = ""

    def run(self, command, *, data=None, timeout=60, check=True):
        try:
            result = subprocess.run(
                command, input=data, capture_output=True, text=True, env=self.environment,
                cwd=REPOSITORY, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Integration command timed out after {timeout}s: "
                + redact(exc.stdout, self.sensitive) + redact(exc.stderr, self.sensitive)
            ) from None
        if check and result.returncode:
            raise RuntimeError(
                "Integration command failed: " + redact(result.stdout + result.stderr, self.sensitive)
            )
        return result

    def compose(self, *arguments, **kwargs):
        return self.run([*self.command, *arguments], **kwargs)

    def sql(self, statement):
        return self.compose(
            "exec", "-T", "database", "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
            data=statement,
        ).stdout.strip()

    def request(self, method, path, *, token=None, **kwargs):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.api.request(method, path, headers=headers, **kwargs)

    def rest(self, method, resource="marketing_contacts", *, token=None, **kwargs):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.http.request(method, f"/rest/v1/{resource}", headers=headers, **kwargs)

    def signup(self, label):
        email = f"{label}-{uuid4().hex}@example.test"
        response = self.http.post("/auth/v1/signup", json={
            "email": email, "password": self.account_password,
            "data": {"full_name": f"Integration {label}", "role": "admin"},
        })
        if response.status_code != 200:
            logs = self.compose("logs", "--no-color", "--tail", "40", "auth", check=False).stdout
            raise RuntimeError(
                f"Fixture sign-up returned HTTP {response.status_code}: "
                + redact(logs, self.sensitive)
            )
        data = response.json()
        token = data.get("access_token")
        assert isinstance(token, str) and token, "GoTrue did not issue a confirmed-session token"
        self.sensitive.append(token)
        user_id = str(UUID(data["user"]["id"]))
        return user_id, token


def assert_local_docker(stack):
    context = stack.environment.get("DOCKER_CONTEXT")
    host = stack.environment.get("DOCKER_HOST")
    if context or not host:
        command = ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]
        if context:
            command.append(context)
        result = stack.run(command)
        host = json.loads(result.stdout)
    if not isinstance(host, str) or not host.startswith("unix://"):
        raise RuntimeError("Integration tests require a local Docker Unix socket")


def wait_for_services(stack):
    deadline = time.monotonic() + 120
    pending = {"/auth/v1/health", "/rest/v1/"}
    while pending and time.monotonic() < deadline:
        for path in tuple(pending):
            try:
                if stack.http.get(path, timeout=2).status_code == 200:
                    pending.remove(path)
            except httpx.HTTPError:
                pass
        if pending:
            time.sleep(0.5)
    if pending:
        raise RuntimeError("Local integration services did not become ready: " + ", ".join(sorted(pending)))


def verify_schema_commit(stack, schema_dir, source_files, sources):
    expected = os.getenv("MAIN_SITE_SCHEMA_COMMIT")
    if expected is None:
        return
    if re.fullmatch(r"[a-fA-F0-9]{40}", expected) is None:
        raise RuntimeError("MAIN_SITE_SCHEMA_COMMIT must be a full 40-character Git commit SHA")
    git = ["git", "-C", str(schema_dir)]
    actual = stack.run([*git, "rev-parse", "HEAD"]).stdout.strip()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"Main-site schema checkout is {actual}, expected {expected}")
    root = Path(stack.run([*git, "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    for source in source_files:
        relative = source.resolve().relative_to(root).as_posix()
        committed = stack.run([*git, "show", f"{actual}:{relative}"]).stdout
        if committed != sources[source.name]:
            raise RuntimeError(f"Loaded SQL differs from the pinned commit: {relative}")
    print("Integration SQL source commit: " + actual)


@pytest.fixture(scope="session")
def local_stack():
    schema_dir = Path(os.getenv(
        "MAIN_SITE_SCHEMA_DIR", str(REPOSITORY.parent / "gtesports-website" / "supabase")
    )).resolve()
    source_files = [schema_dir / "migrations" / name for name in MIGRATIONS]
    source_files += [schema_dir / "tests" / name for name in SQL_CHECKS]
    sources = {}
    for source in source_files:
        if not source.is_file():
            pytest.fail(f"Required pinned main-site SQL source is missing: {source.name}")
        sources[source.name] = source.read_text()
    manifest = {name: hashlib.sha256(sql.encode()).hexdigest() for name, sql in sources.items()}
    print("Integration SQL source SHA256: " + json.dumps(manifest, sort_keys=True))

    stack = Stack()
    verify_schema_commit(stack, schema_dir, source_files, sources)
    assert_local_docker(stack)
    environment = pytest.MonkeyPatch()
    try:
        stack.compose("up", "-d", timeout=300)
        published = stack.compose("port", "proxy", "8080").stdout.strip()
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", published)
        if match is None or not 1 <= int(match[1]) <= 65535:
            status = stack.compose("ps", "--all", check=False).stdout
            logs = stack.compose("logs", "--no-color", "--tail", "40", "proxy", check=False).stdout
            raise RuntimeError(
                "The integration proxy must expose exactly one loopback-only port; "
                f"published={published!r}\n" + redact(status + logs, stack.sensitive)
            )
        stack.base_url = "http://" + published
        stack.http = httpx.Client(
            base_url=stack.base_url, headers={"apikey": PUBLIC_KEY},
            timeout=10, follow_redirects=False, trust_env=False,
        )
        wait_for_services(stack)
        for name in MIGRATIONS:
            stack.sql(sources[name])
        for name in SQL_CHECKS:
            stack.sql(sources[name])
        stack.sql("notify pgrst, 'reload schema';")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            # A recognized private table returns ACL denial for anon, not a missing-table error.
            if stack.rest("GET").status_code in {401, 403}:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("PostgREST did not load the application schema")

        stack.admin_id, stack.admin_token = stack.signup("admin")
        stack.member_id, stack.member_token = stack.signup("member")
        stack.sql(f"insert into public.user_roles(user_id, role) values ('{stack.admin_id}', 'admin');")

        environment.setenv("MAIN_SITE_SUPABASE_URL", stack.base_url)
        environment.setenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", PUBLIC_KEY)
        environment.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:3000")
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            environment.delenv(name, raising=False)
        from fastapi.testclient import TestClient
        from apps.gateway.main import create_app

        stack.api = TestClient(create_app())
        with stack.api:
            yield stack
    finally:
        if stack.http:
            stack.http.close()
        environment.undo()
        result = stack.compose("down", "--volumes", "--remove-orphans", timeout=90, check=False)
        if result.returncode:
            pytest.fail(
                f"Cleanup failed for disposable project {stack.project}: "
                + redact(result.stdout + result.stderr, stack.sensitive)
            )


@pytest.fixture
def database(local_stack):
    local_stack.sql("truncate table public.marketing_contacts;")
    return local_stack


def contact(email="contact@example.test", **changes):
    return {
        "email": email, "name": "Fixture Contact", "organization": "Fixture Club",
        "category": "Partner", "contact_group": "both", "status": "unsubscribed",
        "last_contacted": "2026-10-13", "notes": "Synthetic fixture notes", **changes,
    }


def assert_private(response, status):
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    UUID(response.headers["X-Request-ID"])


def create_contact(database, values=None):
    response = database.request("POST", CONTACT_PATH, token=database.admin_token, json=values or contact())
    assert_private(response, 201)
    return response.json()


def test_me_verifies_real_gotrue_identity_and_current_database_roles(database):
    for user_id, token, expected_role, label in (
        (database.admin_id, database.admin_token, "admin", "admin"),
        (database.member_id, database.member_token, "member", "member"),
    ):
        response = database.request("GET", "/v1/me", token=token)
        assert_private(response, 200)
        data = response.json()
        assert data["user"]["id"] == user_id
        assert data["profile"]["id"] == user_id
        assert data["profile"]["full_name"] == f"Integration {label}"
        assert data["role"] == expected_role
    visible = database.rest("GET", "profiles", token=database.member_token).json()
    assert [profile["id"] for profile in visible] == [database.member_id]
    assert database.rest("GET", "user_roles", token=database.member_token).json() == []


def test_missing_and_invalid_tokens_are_rejected_before_private_data(database):
    for token in (None, "invalid.fixture.token"):
        assert_private(database.request("GET", "/v1/me", token=token), 401)
        assert_private(database.request("GET", CONTACT_PATH, token=token), 401)


def test_member_api_denial_and_direct_database_rls_are_both_effective(database):
    saved = create_contact(database)
    assert_private(database.request("GET", CONTACT_PATH, token=database.member_token), 403)
    assert_private(database.request("POST", CONTACT_PATH, token=database.member_token, json=contact()), 403)
    response = database.rest("GET", token=database.member_token)
    assert response.status_code == 200 and response.json() == []
    assert database.rest("POST", token=database.member_token, json=contact("member@example.test")).status_code == 403
    assert database.rest("PATCH", "user_roles", token=database.member_token, json={"role": "admin"}).status_code == 403
    for method in ("PATCH", "DELETE"):
        response = database.rest(method, token=database.member_token, params={"id": f"eq.{saved['id']}"},
                                 **({"json": {"notes": "unauthorized overwrite"}} if method == "PATCH" else {}))
        assert response.status_code in {200, 204}
    assert database.sql("select notes from public.marketing_contacts;") == "Synthetic fixture notes"


def test_admin_crud_normalization_server_revision_and_stale_conflicts(database):
    saved = create_contact(database, contact("  CONTACT@EXAMPLE.TEST  ", name="  Fixture Contact  "))
    assert saved["email"] == "contact@example.test" and saved["name"] == "Fixture Contact"
    assert saved["revision"] == 1
    conflict = database.request("POST", CONTACT_PATH, token=database.admin_token, json=contact(" CONTACT@EXAMPLE.TEST "))
    assert_private(conflict, 409)
    assert conflict.json()["error"]["code"] == "duplicate_contact"
    path = f"{CONTACT_PATH}/{saved['id']}"
    response = database.request("PATCH", path, token=database.admin_token,
                                json={**contact(notes="Updated fixture note"), "revision": 1})
    assert_private(response, 200)
    updated = response.json()
    assert updated["revision"] == 2 and updated["created_at"] == saved["created_at"]
    assert_private(database.request("PATCH", path, token=database.admin_token,
                                    json={**contact(), "revision": 1}), 409)
    assert_private(database.request("DELETE", path + "?revision=1", token=database.admin_token), 409)
    response = database.request("DELETE", path + "?revision=2", token=database.admin_token)
    assert_private(response, 204)
    assert response.content == b""
    assert database.sql("select count(*) from public.marketing_contacts;") == "0"


def test_concurrent_updates_of_one_revision_have_exactly_one_winner(database):
    saved = create_contact(database)
    barrier = threading.Barrier(2)

    def update(note):
        barrier.wait(timeout=10)
        return database.request("PATCH", f"{CONTACT_PATH}/{saved['id']}", token=database.admin_token,
                                json={**contact(notes=note), "revision": saved["revision"]})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(update, ("first contender", "second contender")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    row = json.loads(database.sql("select row_to_json(marketing_contacts) from public.marketing_contacts;"))
    assert row["revision"] == 2 and row["notes"] == winner["notes"]


def test_import_counts_real_insertions_and_preserves_existing_optouts(database):
    saved = create_contact(database, contact(status="do_not_contact"))
    batch = [
        contact(" CONTACT@EXAMPLE.TEST ", status="active", contact_group="newsletter"),
        contact("new@example.test", status="unsubscribed", contact_group="both"),
        contact(" NEW@EXAMPLE.TEST ", status="active", contact_group="newsletter"),
    ]
    response = database.request("POST", CONTACT_PATH + "/import", token=database.admin_token, json={"contacts": batch})
    assert_private(response, 200)
    assert response.json() == {"imported": 1, "skipped": 2}
    replay = database.request("POST", CONTACT_PATH + "/import", token=database.admin_token, json={"contacts": batch})
    assert replay.status_code == 200 and replay.json() == {"imported": 0, "skipped": 3}
    rows = database.rest("GET", token=database.admin_token, params={"order": "email.asc"}).json()
    assert len(rows) == 2
    assert rows[0] == saved
    assert rows[1]["status"] == "unsubscribed" and rows[1]["contact_group"] == "both"


def test_database_constraint_failure_rolls_back_entire_bulk_import(database):
    database.sql("alter table public.marketing_contacts add constraint integration_rejected_email check (email <> 'reject-in-database@example.test');")
    try:
        response = database.request("POST", CONTACT_PATH + "/import", token=database.admin_token, json={"contacts": [
            contact("would-have-inserted@example.test"), contact("reject-in-database@example.test"),
        ]})
        assert_private(response, 422)
        assert "would-have-inserted" not in response.text and "reject-in-database" not in response.text
        assert database.sql("select count(*) from public.marketing_contacts;") == "0"
    finally:
        database.sql("alter table public.marketing_contacts drop constraint integration_rejected_email;")


def test_pagination_uses_actual_two_row_cap_and_handles_empty_end(database):
    batch = [contact(f"page-{index}@example.test") for index in range(5)]
    imported = database.request("POST", CONTACT_PATH + "/import", token=database.admin_token, json={"contacts": batch})
    assert imported.status_code == 200 and imported.json() == {"imported": 5, "skipped": 0}
    rows, sizes, offset = [], [], 0
    while offset is not None:
        response = database.request("GET", CONTACT_PATH, token=database.admin_token, params={"offset": offset, "limit": 500})
        assert_private(response, 200)
        page = response.json()
        rows.extend(page["contacts"])
        sizes.append(len(page["contacts"]))
        offset = page["next_offset"]
        assert len(sizes) <= 3, "Pagination did not terminate"
    assert sizes == [2, 2, 1]
    assert [row["email"] for row in rows] == [row["email"] for row in batch]
    beyond = database.request("GET", CONTACT_PATH, token=database.admin_token, params={"offset": 99})
    assert_private(beyond, 200)
    assert beyond.json() == {"contacts": [], "next_offset": None}
    database.sql("truncate table public.marketing_contacts;")
    empty = database.request("GET", CONTACT_PATH, token=database.admin_token)
    assert_private(empty, 200)
    assert empty.json() == {"contacts": [], "next_offset": None}


def test_role_revocation_takes_effect_for_the_same_real_session(database):
    create_contact(database)
    database.sql(f"update public.user_roles set role='member' where user_id='{database.admin_id}';")
    try:
        me = database.request("GET", "/v1/me", token=database.admin_token)
        assert_private(me, 200)
        assert me.json()["role"] == "member"
        assert_private(database.request("GET", CONTACT_PATH, token=database.admin_token), 403)
        assert_private(database.request("POST", CONTACT_PATH, token=database.admin_token, json=contact("revoked@example.test")), 403)
        direct = database.rest("GET", token=database.admin_token)
        assert direct.status_code == 200 and direct.json() == []
        assert database.rest("POST", token=database.admin_token, json=contact("revoked@example.test")).status_code == 403
        assert database.sql("select count(*) from public.marketing_contacts;") == "1"
    finally:
        database.sql(f"update public.user_roles set role='admin' where user_id='{database.admin_id}';")
