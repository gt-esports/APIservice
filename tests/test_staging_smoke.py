import io
from uuid import uuid4

import httpx
import pytest

from scripts import staging_smoke

ORIGIN = "https://www.gatechesports.com"
USER_ID = "10000000-0000-4000-8000-000000000001"
TOKEN = "synthetic-smoke-access-token"


def fixture_transport(requests, replacement=None):
    def handler(request):
        requests.append(request)
        if replacement:
            result = replacement(request)
            if result is not None:
                return result
        headers = {"X-Request-ID": str(uuid4()), "Access-Control-Allow-Origin": ORIGIN, "Cache-Control": "no-store"}
        assert request.method in {"GET", "OPTIONS"}
        if request.method == "OPTIONS":
            if request.headers["Origin"] != ORIGIN:
                return httpx.Response(400, headers={"Cache-Control": "no-store"})
            return httpx.Response(200, headers={**headers, "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE", "Access-Control-Allow-Headers": "Authorization, Content-Type"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"service": "gateway", "status": "ok"}, headers=headers)
        if "Authorization" not in request.headers:
            return httpx.Response(401, json={"error": {"code": "unauthorized"}}, headers=headers)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if request.url.path == "/v1/me":
            return httpx.Response(200, json={"user": {"id": USER_ID, "email": "private@example.test"}, "profile": None, "role": "admin"}, headers=headers)
        if request.url.path == "/v1/main/ready":
            return httpx.Response(200, json={"service": "main-site", "status": "ready"}, headers=headers)
        assert request.url.path == "/v1/main/marketing-contacts"
        assert request.url.params["limit"] == "1"
        return httpx.Response(200, json={"contacts": [{"email": "private@example.test", "notes": "private note"}], "next_offset": None}, headers=headers)
    return httpx.MockTransport(handler)


def test_smoke_checks_live_contract_without_writes_or_private_output(monkeypatch, capsys):
    requests = []
    real_client = httpx.Client
    monkeypatch.setattr(staging_smoke.httpx, "Client", lambda **kwargs: real_client(transport=fixture_transport(requests), **kwargs))
    status = staging_smoke.main([], {
        "ESPORTS_API_URL": "https://api.example.test", "ESPORTS_FRONTEND_ORIGIN": ORIGIN,
        "ESPORTS_SMOKE_ACCESS_TOKEN": TOKEN,
    })
    assert status == 0
    output = capsys.readouterr()
    assert "Read-only checks passed" in output.out
    assert TOKEN not in output.out + output.err
    assert "private@example.test" not in output.out + output.err
    assert "private note" not in output.out + output.err
    assert len(requests) == 10
    assert all(request.method in {"GET", "OPTIONS"} for request in requests)
    assert sum("Authorization" in request.headers for request in requests) == 3


def test_stdin_token_input(monkeypatch):
    requests = []
    real_client = httpx.Client
    monkeypatch.setattr(staging_smoke.httpx, "Client", lambda **kwargs: real_client(transport=fixture_transport(requests), **kwargs))
    monkeypatch.setattr(staging_smoke.sys, "stdin", io.StringIO(TOKEN + "\n"))
    assert staging_smoke.main(["--base-url", "http://127.0.0.1:8000", "--origin", ORIGIN, "--token-stdin"], {}) == 0


@pytest.mark.parametrize("url", [
    "", "http://public.example.test", "https://user:secret@api.example.test",
    "https://api.example.test/private", "https://api.example.test?token=private",
    "*", "https://api.example.test:bad", "https://api.example.test?",
    "https://api.example.test#", "https://api.example.test/?",
    "https://api.example.test/#", " https://api.example.test", "https://api.example.test ",
    "https://api.example.test\n", "https://api.\texample.test", "https://api.\r\nexample.test",
    "https://api. example.test", "https://api.example.test\x00", "https://api.example.test\x7f",
    "https://api.example.test\x85", "https://api.example.test\u00a0",
])
def test_invalid_origin_does_not_echo_url(url, monkeypatch, capsys):
    monkeypatch.setattr(staging_smoke.httpx, "Client", lambda **kwargs: pytest.fail("Invalid origin must not start an HTTP client"))
    result = staging_smoke.main(["--base-url", url, "--origin", ORIGIN], {"ESPORTS_SMOKE_ACCESS_TOKEN": TOKEN})
    assert result == 2
    output = capsys.readouterr().err
    assert "private" not in output
    assert "secret" not in output
    assert TOKEN not in output


def test_missing_token_fails_noninteractive(monkeypatch, capsys):
    monkeypatch.setattr(staging_smoke.sys, "stdin", io.StringIO(""))
    assert staging_smoke.main(["--base-url", "https://api.example.test", "--origin", ORIGIN], {}) == 2
    assert "Configuration error" in capsys.readouterr().err


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://redirect.example/private"}),
    httpx.Response(503, json={"detail": "private upstream credentials"}),
    httpx.Response(200, json={"service": "gateway", "status": "ok"}),
])
def test_smoke_fails_safely_on_redirect_failure_or_missing_cors(monkeypatch, capsys, response):
    requests = []
    real_client = httpx.Client
    monkeypatch.setattr(staging_smoke.httpx, "Client", lambda **kwargs: real_client(transport=fixture_transport(requests, lambda _: response), **kwargs))
    assert staging_smoke.main(["--base-url", "https://api.example.test", "--origin", ORIGIN], {"ESPORTS_SMOKE_ACCESS_TOKEN": TOKEN}) == 1
    assert len(requests) == 1
    output = capsys.readouterr().err
    assert "private" not in output
    assert "credentials" not in output
    assert TOKEN not in output


def test_smoke_requires_no_store_on_private_responses():
    def omit_no_store(request):
        if request.url.path == "/v1/me":
            return httpx.Response(401, json={"error": {"code": "unauthorized"}}, headers={"X-Request-ID": str(uuid4()), "Access-Control-Allow-Origin": ORIGIN})
    with httpx.Client(base_url="https://api.example.test", transport=fixture_transport([], omit_no_store)) as client:
        with pytest.raises(staging_smoke.SmokeFailure, match="no-store"):
            staging_smoke.run_checks(client, ORIGIN, TOKEN)


def test_http_exception_message_is_not_printed(monkeypatch, capsys):
    def fail(request):
        raise httpx.ConnectError("private-token-in-url")
    real_client = httpx.Client
    monkeypatch.setattr(staging_smoke.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fail), **kwargs))
    assert staging_smoke.main(["--base-url", "https://api.example.test", "--origin", ORIGIN], {"ESPORTS_SMOKE_ACCESS_TOKEN": TOKEN}) == 1
    assert "private-token-in-url" not in capsys.readouterr().err
