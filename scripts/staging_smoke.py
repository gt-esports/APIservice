"""Read-only staging checks. Tokens are accepted through env, stdin, or a hidden prompt."""

import argparse
import getpass
import os
import re
import sys
import unicodedata
from collections.abc import Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx


class SmokeFailure(Exception):
    pass


def origin_url(value: str) -> str:
    if any(char in "?#" or char.isspace() or unicodedata.category(char) == "Cc" for char in value):
        raise SmokeFailure("Origin must not contain query or fragment delimiters, whitespace, or control characters.")
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        permitted = (
            bool(parsed.hostname)
            and "*" not in value
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        )
        parsed.port
    except ValueError:
        permitted = False
    if not permitted:
        raise SmokeFailure("Use an exact HTTPS origin, or HTTP on loopback, without credentials or a path.")
    return value


def json_object(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise SmokeFailure("Response is not valid JSON.") from None
    if not isinstance(value, dict):
        raise SmokeFailure("Response is not a JSON object.")
    return value


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def check_response(response: httpx.Response, status: int, *, private: bool, origin: str) -> None:
    expect(response.status_code == status, f"Expected HTTP {status}, received HTTP {response.status_code}.")
    expect(response.headers.get("Access-Control-Allow-Origin") == origin, "Configured frontend origin was not allowed.")
    try:
        UUID(response.headers.get("X-Request-ID", ""))
    except ValueError:
        raise SmokeFailure("Response is missing a valid request ID.") from None
    if private:
        expect(response.headers.get("Cache-Control") == "no-store", "Private response is missing no-store.")


def run_checks(client: httpx.Client, origin: str, token: str) -> list[str]:
    passed = []
    base_headers = {"Origin": origin}
    auth_headers = {**base_headers, "Authorization": f"Bearer {token}"}

    response = client.get("/health", headers=base_headers)
    check_response(response, 200, private=False, origin=origin)
    expect(json_object(response) == {"service": "gateway", "status": "ok"}, "Unexpected liveness response.")
    passed.append("gateway liveness")

    response = client.get("/v1/me", headers=base_headers)
    check_response(response, 401, private=True, origin=origin)
    error = json_object(response).get("error")
    expect(isinstance(error, dict) and error.get("code") == "unauthorized", "Anonymous account request was not denied correctly.")
    passed.append("anonymous denial")

    for method in ("GET", "POST", "PATCH", "DELETE"):
        response = client.options(
            "/v1/main/marketing-contacts",
            headers={**base_headers, "Access-Control-Request-Method": method,
                     "Access-Control-Request-Headers": "authorization,content-type"},
        )
        check_response(response, 200, private=True, origin=origin)
        allowed_headers = {value.strip().lower() for value in response.headers.get("Access-Control-Allow-Headers", "").split(",")}
        allowed_methods = {value.strip() for value in response.headers.get("Access-Control-Allow-Methods", "").split(",")}
        expect({"authorization", "content-type"} <= allowed_headers and method in allowed_methods, "Required CORS method or headers were not allowed.")
    passed.append("configured-origin CORS preflights")

    response = client.options(
        "/v1/main/marketing-contacts",
        headers={"Origin": f"https://smoke-denied-{uuid4().hex}.invalid",
                 "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "authorization"},
    )
    expect(response.status_code == 400 and "Access-Control-Allow-Origin" not in response.headers, "Unconfigured origin was not rejected.")
    expect(response.headers.get("Cache-Control") == "no-store", "Private preflight response is missing no-store.")
    passed.append("unconfigured-origin denial")

    response = client.get("/v1/me", headers=auth_headers)
    check_response(response, 200, private=True, origin=origin)
    account = json_object(response)
    user = account.get("user")
    expect(isinstance(user, dict), "Account response is missing identity.")
    try:
        UUID(user.get("id", ""))
    except (ValueError, TypeError, AttributeError):
        raise SmokeFailure("Account identity is invalid.") from None
    expect(account.get("role") == "admin", "Use an existing staging administrator session.")
    expect(user.get("email") is None or isinstance(user.get("email"), str), "Account email shape is invalid.")
    profile = account.get("profile")
    expect(profile is None or (isinstance(profile, dict) and profile.get("id") == user["id"]), "Profile does not match the verified identity.")
    passed.append("current administrator identity and profile")

    response = client.get("/v1/main/ready", headers=auth_headers)
    check_response(response, 200, private=True, origin=origin)
    expect(json_object(response) == {"service": "main-site", "status": "ready"}, "Unexpected main-site readiness response.")
    passed.append("main-site schema and read readiness")

    response = client.get("/v1/main/marketing-contacts", params={"limit": "1", "offset": "0"}, headers=auth_headers)
    check_response(response, 200, private=True, origin=origin)
    page = json_object(response)
    contacts = page.get("contacts")
    expect(isinstance(contacts, list) and len(contacts) <= 1, "Contact page is malformed.")
    next_offset = page.get("next_offset")
    expect("next_offset" in page and (next_offset is None or (type(next_offset) is int and next_offset == len(contacts) and next_offset > 0)), "Contact pagination is malformed.")
    passed.append("read-only contact page")
    return passed


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=environ.get("ESPORTS_API_URL", ""), help="HTTPS API origin; defaults to ESPORTS_API_URL")
    parser.add_argument("--origin", default=environ.get("ESPORTS_FRONTEND_ORIGIN", ""), help="Exact frontend origin; defaults to ESPORTS_FRONTEND_ORIGIN")
    parser.add_argument("--token-stdin", action="store_true", help="Read the access token from stdin instead of env or a hidden prompt")
    args = parser.parse_args(argv)
    try:
        base_url = origin_url(args.base_url)
        origin = origin_url(args.origin)
        if args.token_stdin:
            token = sys.stdin.read(16385).strip()
        else:
            token = environ.get("ESPORTS_SMOKE_ACCESS_TOKEN", "").strip()
            if not token and sys.stdin.isatty():
                token = getpass.getpass("Staging administrator access token (hidden): ").strip()
        expect(bool(token) and len(token) <= 16384 and bool(re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,2}", token)), "Provide a valid access token through ESPORTS_SMOKE_ACCESS_TOKEN, stdin, or the hidden prompt.")
    except (SmokeFailure, EOFError):
        print("Configuration error: supply valid API/frontend origins and an administrator token through env, stdin, or the hidden prompt.", file=sys.stderr)
        return 2

    try:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False) as client:
            passed = run_checks(client, origin, token)
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError:
        print("FAIL: API connection or request timed out; no response details were printed.", file=sys.stderr)
        return 1
    for check in passed:
        print(f"PASS: {check}")
    print("Read-only checks passed. Contact values and tokens were not printed; writes and browser behavior were not tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
