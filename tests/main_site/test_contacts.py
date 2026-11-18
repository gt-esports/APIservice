import copy
import json
import logging
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.gateway.main import create_app
from apps.main_site.body_limit import MAX_CONTACT_BODY_BYTES
from apps.main_site.client import MainSiteClient, get_main_site_client


PATH = "/v1/main/marketing-contacts"
USER_ID = "fd701955-2d3b-49c9-b148-7c5d14950029"
CONTACT_ID = "5b43f2ad-d9af-4adc-a643-e530f4efdc28"
TOKEN = "fixture.session.token"
KEY = "sb_publishable_fixture"
INPUT = {
    "email": "contact@example.invalid", "name": "Contact", "organization": "Club",
    "category": "Partner", "contact_group": "both", "status": "unsubscribed",
    "last_contacted": "2026-10-13", "notes": "Private outreach notes",
}
ROW = {
    **INPUT, "id": CONTACT_ID, "revision": 3,
    "created_at": "2026-10-13T12:00:00+00:00", "updated_at": "2026-10-14T12:00:00+00:00",
}


@pytest.fixture
def api():
    def make(contacts, role="admin", auth_status=200):
        requests = []
        current = {"role": role}

        def upstream(request):
            requests.append(request)
            assert request.headers["apikey"] == KEY
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            assert "x-user-id" not in request.headers
            assert "x-user-role" not in request.headers
            if request.url.path == "/auth/v1/user":
                assert request.method == "GET"
                return httpx.Response(auth_status, json={
                    "id": USER_ID, "email": "admin@example.invalid",
                    "user_metadata": {"role": "admin"},
                })
            if request.url.path == "/rest/v1/user_roles":
                assert request.method == "GET"
                assert dict(request.url.params) == {
                    "select": "user_id,role", "user_id": f"eq.{USER_ID}", "limit": "2",
                }
                return httpx.Response(200, json=[] if current["role"] is None else [{
                    "user_id": USER_ID, "role": current["role"],
                }])
            assert request.url.path == "/rest/v1/marketing_contacts"
            return contacts(request)

        async def client_dependency():
            async with httpx.AsyncClient(
                base_url="https://fixture.supabase.invalid",
                transport=httpx.MockTransport(upstream),
            ) as client:
                yield MainSiteClient(client, KEY)

        app = create_app()
        app.dependency_overrides[get_main_site_client] = client_dependency
        client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
        return client, requests, current
    return make


def contact_requests(requests):
    return [request for request in requests if request.url.path == "/rest/v1/marketing_contacts"]


def assert_private(response):
    assert response.headers["Cache-Control"] == "no-store"
    UUID(response.headers["X-Request-ID"])


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert_private(response)
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]


def forbidden_contact_request(_):
    pytest.fail("Unauthorized or invalid requests must not access contact storage")


def test_list_preserves_identity_rls_projection_and_database_page_cap(api):
    def upstream(request):
        assert request.method == "GET"
        assert request.headers["prefer"] == "count=exact"
        params = dict(request.url.params)
        assert params["order"] == "email.asc,id.asc"
        assert params["limit"] == "500"
        assert "*" not in params["select"]
        assert "secret" not in params["select"]
        offset = int(params["offset"])
        rows = [{**ROW, "id": str(UUID(int=i + 1)), "email": f"{i}@example.invalid", "secret": "not exposed"}
                for i in range(offset, min(offset + 2, 5))]
        return httpx.Response(206, json=rows, headers={"Content-Range": f"{offset}-{offset + len(rows) - 1}/5"})

    client, requests, _ = api(upstream)
    with client:
        combined, offset = [], 0
        while offset is not None:
            response = client.get(PATH, params={"offset": offset, "limit": 500}, headers={"X-User-ID": "spoof", "X-User-Role": "admin"})
            assert response.status_code == 200
            assert_private(response)
            assert "secret" not in response.text
            combined.extend(response.json()["contacts"])
            offset = response.json()["next_offset"]
    assert len(combined) == 5
    assert [r.url.params["offset"] for r in contact_requests(requests)] == ["0", "2", "4"]
    assert len([r for r in requests if r.url.path == "/auth/v1/user"]) == 3


@pytest.mark.parametrize("status,body,content_range,offset", [
    (200, [], "*/0", 0), (416, {"code": "PGRST103"}, "*/2", 5),
])
def test_empty_or_concurrently_removed_last_page(api, status, body, content_range, offset):
    client, _, _ = api(lambda _: httpx.Response(status, json=body, headers={"Content-Range": content_range}))
    with client:
        response = client.get(PATH, params={"offset": offset})
    assert response.status_code == 200
    assert response.json() == {"contacts": [], "next_offset": None}
    assert_private(response)


@pytest.mark.parametrize("body,content_range", [
    ([ROW], ""), ([ROW], "0-0/*"), ([ROW], "1-1/2"), ([ROW], "0-1/2"),
    ([ROW], "0-0/0"), ([], "*/1"), ([ROW, ROW], "0-1/2"),
])
def test_list_rejects_incomplete_or_inconsistent_upstream_pages(api, body, content_range):
    client, _, _ = api(lambda _: httpx.Response(200, json=body, headers={"Content-Range": content_range}))
    with client:
        assert_error(client.get(PATH), 503, "upstream_unavailable")


@pytest.mark.parametrize("role", [None, "member"])
@pytest.mark.parametrize("method,suffix,payload", [
    ("GET", "", None), ("POST", "", INPUT), ("POST", "/import", {"contacts": [INPUT]}),
    ("PATCH", f"/{CONTACT_ID}", {**INPUT, "revision": 3}),
    ("DELETE", f"/{CONTACT_ID}?revision=3", None),
])
def test_members_and_metadata_admins_cannot_access_contacts(api, role, method, suffix, payload):
    client, requests, _ = api(forbidden_contact_request, role=role)
    with client:
        response = client.request(method, PATH + suffix, json=payload)
    assert_error(response, 403, "forbidden")
    assert not contact_requests(requests)


def test_revoked_admin_same_token_denied_on_next_request(api):
    client, requests, current = api(lambda _: httpx.Response(200, json=[ROW], headers={"Content-Range": "0-0/1"}))
    with client:
        assert client.get(PATH).status_code == 200
        current["role"] = "member"
        assert_error(client.post(PATH, json=INPUT), 403, "forbidden")
    assert len(contact_requests(requests)) == 1


def test_invalid_session_never_queries_roles_or_contacts(api):
    client, requests, _ = api(forbidden_contact_request, auth_status=401)
    with client:
        response = client.get(PATH)
    assert_error(response, 401, "unauthorized")
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert len(requests) == 1


def test_create_normalizes_only_editable_fields_and_returns_database_revision(api):
    def upstream(request):
        assert request.method == "POST"
        assert json.loads(request.content) == INPUT
        assert request.headers["prefer"] == "return=representation"
        return httpx.Response(201, json=[{**ROW, "revision": 1, "internal": "omit"}])
    client, _, _ = api(upstream)
    with client:
        response = client.post(PATH, json={**INPUT, "email": " CONTACT@EXAMPLE.INVALID ", "name": " Contact "})
    assert response.status_code == 201
    assert response.json() == {**ROW, "revision": 1}
    assert_private(response)


@pytest.mark.parametrize("field,value", [
    ("email", "bad"), ("email", "a" * 250 + "@example.invalid"), ("email", None),
    ("name", "x" * 201), ("name", 123), ("organization", "x" * 201),
    ("category", "x" * 101), ("notes", "x" * 5001), ("status", "new"),
    ("contact_group", "unknown"), ("last_contacted", "2026-02-30"),
    ("last_contacted", "2026-10-13T00:00:00Z"), ("last_contacted", 123),
    ("id", CONTACT_ID), ("revision", 99), ("created_at", ROW["created_at"]),
])
def test_invalid_contact_fields_and_server_metadata_do_not_write(api, field, value):
    client, requests, _ = api(forbidden_contact_request)
    with client:
        response = client.post(PATH, json={**INPUT, field: value})
    assert_error(response, 422, "validation_error")
    assert INPUT["email"] not in response.text
    assert INPUT["notes"] not in response.text
    assert not contact_requests(requests)


def test_patch_filters_revision_atomically_and_does_not_send_server_metadata(api):
    def upstream(request):
        assert request.method == "PATCH"
        assert request.url.params["id"] == f"eq.{CONTACT_ID}"
        assert request.url.params["revision"] == "eq.3"
        assert json.loads(request.content) == {**INPUT, "notes": "Updated"}
        return httpx.Response(200, json=[{**ROW, "notes": "Updated", "revision": 4}])
    client, requests, _ = api(upstream)
    with client:
        response = client.patch(f"{PATH}/{CONTACT_ID}", json={**INPUT, "notes": "Updated", "revision": 3})
    assert response.status_code == 200
    assert response.json()["revision"] == 4
    assert len(contact_requests(requests)) == 1
    assert_private(response)


@pytest.mark.parametrize("revision", [None, True, False, 0, -1, 1.5, "3", 2147483648])
def test_patch_requires_strict_positive_integer_revision(api, revision):
    client, requests, _ = api(forbidden_contact_request)
    with client:
        response = client.patch(f"{PATH}/{CONTACT_ID}", json={**INPUT, "revision": revision})
    assert_error(response, 422, "validation_error")
    assert not contact_requests(requests)


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_stale_or_deleted_record_conflicts_instead_of_reporting_success(api, method):
    client, requests, _ = api(lambda _: httpx.Response(200, json=[]))
    with client:
        response = client.request(method, f"{PATH}/{CONTACT_ID}" + ("?revision=3" if method == "DELETE" else ""),
                                  json={**INPUT, "revision": 3} if method == "PATCH" else None)
    assert_error(response, 409, "revision_conflict")
    assert len(contact_requests(requests)) == 1


def test_delete_only_returns_204_after_matching_revision_and_id(api):
    def upstream(request):
        assert request.method == "DELETE"
        assert dict(request.url.params) == {"id": f"eq.{CONTACT_ID}", "revision": "eq.3", "select": "id"}
        assert request.headers["prefer"] == "return=representation"
        return httpx.Response(200, json=[{"id": CONTACT_ID}])
    client, _, _ = api(upstream)
    with client:
        response = client.delete(f"{PATH}/{CONTACT_ID}?revision=3")
    assert response.status_code == 204
    assert response.content == b""
    assert_private(response)


@pytest.mark.parametrize("suffix", ["", "?revision=0", "?revision=-1", "?revision=true", "?revision=1.5", "?revision=2147483648"])
def test_delete_requires_valid_revision(api, suffix):
    client, requests, _ = api(forbidden_contact_request)
    with client:
        assert_error(client.delete(f"{PATH}/{CONTACT_ID}{suffix}"), 422, "validation_error")
    assert not contact_requests(requests)


@pytest.mark.parametrize("method,body", [
    ("PATCH", [{**ROW, "revision": 3}]), ("PATCH", [{**ROW, "id": USER_ID, "revision": 4}]),
    ("DELETE", [{"id": USER_ID}]), ("DELETE", {"id": CONTACT_ID}),
])
def test_malformed_mutation_receipt_never_reports_success(api, method, body):
    client, _, _ = api(lambda _: httpx.Response(200, json=body))
    with client:
        response = client.request(method, f"{PATH}/{CONTACT_ID}" + ("?revision=3" if method == "DELETE" else ""),
                                  json={**INPUT, "revision": 3} if method == "PATCH" else None)
    assert_error(response, 503, "upstream_unavailable")


def test_import_uses_one_atomic_ignore_duplicates_statement_and_counts_original_rows(api):
    existing = copy.deepcopy(ROW)
    database = {existing["email"]: existing}
    first = {**INPUT, "email": "new@example.invalid", "status": "do_not_contact", "contact_group": "both"}
    duplicate = {**first, "email": " NEW@EXAMPLE.INVALID ", "status": "active", "contact_group": "newsletter"}
    existing_attempt = {**INPUT, "email": " CONTACT@EXAMPLE.INVALID ", "status": "active", "contact_group": "newsletter"}

    def upstream(request):
        assert request.method == "POST"
        assert dict(request.url.params) == {"on_conflict": "email"}
        assert set(request.headers["prefer"].split(",")) == {"resolution=ignore-duplicates", "return=minimal", "count=exact"}
        payload = json.loads(request.content)
        assert len(payload) == 2
        assert payload[0] == first
        inserted = 0
        for row in payload:
            if row["email"] not in database:
                database[row["email"]] = row
                inserted += 1
        return httpx.Response(201, headers={"Content-Range": f"*/{inserted}"})

    client, requests, _ = api(upstream)
    with client:
        response = client.post(PATH + "/import", json={"contacts": [first, duplicate, existing_attempt]})
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "skipped": 2}
    assert database[existing["email"]] == existing
    assert database[first["email"]]["status"] == "do_not_contact"
    assert len(contact_requests(requests)) == 1
    assert_private(response)


@pytest.mark.parametrize("rows", [[], [INPUT] * 501, [INPUT, {**INPUT, "last_contacted": "2026-02-30"}],
                                  [INPUT, {**INPUT, "notes": "x" * 5001}],
                                  [INPUT, {**INPUT, "revision": 30}]])
def test_invalid_import_rejects_whole_batch_before_any_write(api, rows):
    client, requests, _ = api(forbidden_contact_request)
    with client:
        response = client.post(PATH + "/import", json={"contacts": rows})
    assert_error(response, 422, "validation_error")
    assert not contact_requests(requests)


@pytest.mark.parametrize("content_range", ["", "*/2", "*/-1", "0-0/1", "*/*"])
def test_import_requires_valid_committed_row_count(api, content_range):
    client, _, _ = api(lambda _: httpx.Response(201, headers={"Content-Range": content_range}))
    with client:
        response = client.post(PATH + "/import", json={"contacts": [INPUT]})
    assert_error(response, 503, "upstream_unavailable")


@pytest.mark.parametrize("status,code,expected_status,expected_code", [
    (409, "23505", 409, "duplicate_contact"), (403, "42501", 403, "forbidden"),
    (400, "23514", 422, "validation_error"), (401, "PGRST301", 401, "unauthorized"),
    (500, "XX000", 503, "upstream_unavailable"), (302, "", 503, "upstream_unavailable"),
])
def test_database_errors_sanitize_private_details_and_are_not_cached(api, caplog, status, code, expected_status, expected_code):
    secret = "private-detail-contact@example.invalid"
    client, _, _ = api(lambda _: httpx.Response(status, json={"code": code, "message": secret, "details": INPUT["notes"]}))
    with caplog.at_level(logging.INFO), client:
        response = client.post(PATH, json=INPUT)
    assert_error(response, expected_status, expected_code)
    for value in [secret, INPUT["notes"], INPUT["email"], TOKEN]:
        assert value not in response.text
        assert value not in caplog.text


def test_write_timeout_is_sanitized_and_never_retried(api, caplog):
    def upstream(request):
        raise httpx.ReadTimeout("private timeout details " + INPUT["email"], request=request)
    client, requests, _ = api(upstream)
    with caplog.at_level(logging.INFO), client:
        response = client.post(PATH + "/import", json={"contacts": [INPUT]})
    assert_error(response, 504, "upstream_timeout")
    assert len(contact_requests(requests)) == 1
    assert INPUT["email"] not in response.text + caplog.text
    assert "No contacts" not in response.text


@pytest.mark.parametrize("streamed", [False, True])
def test_oversize_contact_body_rejected_before_auth_or_storage(api, streamed):
    client, requests, _ = api(forbidden_contact_request)
    body = b"x" * (MAX_CONTACT_BODY_BYTES + 1)
    with client:
        response = client.post(PATH + "/import", content=iter([body[:100], body[100:]]) if streamed else body,
                               headers={"Content-Type": "application/json"})
    assert_error(response, 413, "payload_too_large")
    assert requests == []
