"""Aperture as a Lucent tool (beta): pairing, the connector API and "Sign in
with Lucent". Lucent itself is played by a stand-in for `lucent._post`, so
this suite needs no second service; the lucent repository tests the other
side of the same contract.
"""

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from aperture.console import lucent, security
from aperture.console.app import app as console_app

PASSWORD = "console-test-password"
ISSUER = "http://lucent.test"
CLIENT_SECRET = "s" * 43


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def hs256(claims: dict, secret: str = CLIENT_SECRET, alg: str = "HS256") -> str:
    head = _b64u(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = _b64u(json.dumps(claims).encode())
    sig = _b64u(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class FakeLucent:
    """Answers the two calls Aperture makes: pairing, and the token exchange."""

    def __init__(self):
        self.sent = []
        self.connector_secret = None
        self.claims = {}

    def __call__(self, url, *, json_body=None, form=None, basic=None):
        self.sent.append((url, json_body, form, basic))
        if url.endswith("/api/connect/pair"):
            self.connector_secret = json_body["connector_secret"]
            assert json_body["id_token_alg"] == "HS256"
            return {"tool_id": 7, "client_id": "aperture-abc", "client_secret": CLIENT_SECRET,
                    "issuer": ISSUER, "id_token_alg": "HS256", "contract": 1}
        if url.endswith("/oidc/token"):
            assert basic == ("aperture-abc", CLIENT_SECRET)
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": "aperture-abc", "sub": "1", "email": "lucya@example.org",
                      "iat": now, "exp": now + 300, **self.claims}
            return {"id_token": hs256(claims), "token_type": "Bearer"}
        raise AssertionError(url)


@pytest.fixture
def paired(indexed, monkeypatch):
    fake = FakeLucent()
    monkeypatch.setattr(lucent, "_post", fake)
    security.set_password(PASSWORD)
    security.reset()
    lucent._pending.clear()
    lucent.join(ISSUER, "ABCD-EFGH-JKLM", ui_url="http://testserver")
    yield fake, TestClient(console_app)
    lucent.leave()
    security.clear_password()
    security.reset()


def bearer(fake):
    return {"authorization": f"Bearer {fake.connector_secret}"}


# ---- pairing ----------------------------------------------------------------
def test_unpaired_aperture_has_no_connector(console):
    lucent.leave()
    assert console.get("/lucent/v1/manifest").status_code == 404
    assert console.get("/auth/lucent", follow_redirects=False).status_code == 404


def test_join_keeps_only_a_hash_of_the_connector_secret(paired):
    fake, _ = paired
    raw = lucent._path().read_text()
    assert fake.connector_secret not in raw
    assert json.loads(raw)["connector_secret_sha256"] == hashlib.sha256(fake.connector_secret.encode()).hexdigest()
    if os.name == "posix":
        assert stat.S_IMODE(lucent._path().stat().st_mode) == 0o600
    url, body, _, _ = fake.sent[0]
    assert url == f"{ISSUER}/api/connect/pair"
    assert body["redirect_uris"] == ["http://testserver/auth/lucent/callback"]


# ---- the connector API -------------------------------------------------------
def test_connector_needs_the_secret(paired):
    fake, c = paired
    assert c.get("/lucent/v1/status").status_code == 401
    assert c.get("/lucent/v1/status", headers={"authorization": "Bearer nope"}).status_code == 401
    # and the secret is not a session: the console itself stays closed
    assert c.get("/api/meta", headers=bearer(fake)).status_code == 401


def test_manifest_and_status(paired):
    fake, c = paired
    m = c.get("/lucent/v1/manifest", headers=bearer(fake)).json()
    assert m["kind"] == "aperture" and m["contract"] == 1
    assert {a["id"] for a in m["actions"]} == {"scan", "pause", "resume"}
    s = c.get("/lucent/v1/status", headers=bearer(fake)).json()
    assert s["state"] in ("ok", "warn", "down")
    assert [card["label"] for card in s["cards"]] == ["Fotos", "Archiv", "Indexer", "Speicher"]
    assert s["cards"][0]["value"] == "8"


def test_scan_goes_through_the_control_channel(paired):
    fake, c = paired
    r = c.post("/lucent/v1/actions/scan", headers=bearer(fake), json={"by": "lucent:lucya@example.org"})
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["request_id"]
    assert c.post("/lucent/v1/actions/rm", headers=bearer(fake)).status_code == 404
    log = (security._audit_path()).read_text().splitlines()
    assert any('"scan requested"' in line and "lucent" in line for line in log)


def test_pause_and_resume(paired):
    fake, c = paired
    assert c.post("/lucent/v1/actions/pause", headers=bearer(fake)).json()["ok"]
    assert c.get("/lucent/v1/status", headers=bearer(fake)).json()["state"] == "warn"
    assert c.post("/lucent/v1/actions/resume", headers=bearer(fake)).json()["message"] == "Indexer läuft weiter."


# ---- sign in with Lucent ------------------------------------------------------
def _start(c):
    r = c.get("/auth/lucent", follow_redirects=False)
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{ISSUER}/oidc/authorize"
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "aperture-abc"
    return q


def test_login_page_offers_lucent(paired):
    _, c = paired
    assert "Sign in with Lucent" in c.get("/login").text


def test_sign_in_with_lucent(paired):
    fake, c = paired
    q = _start(c)
    fake.claims = {"nonce": q["nonce"]}
    r = c.get("/auth/lucent/callback", params={"code": "abc", "state": q["state"], "iss": ISSUER})
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.text
    form = fake.sent[-1][2]
    verifier = form["code_verifier"]
    assert _b64u(hashlib.sha256(verifier.encode()).digest()) == q["code_challenge"]
    s = c.get("/api/session").json()
    assert s["signed_in"] and s["via"] == "lucent" and s["who"] == "lucya@example.org"
    assert c.get("/api/meta").status_code == 200
    # the password still opens the door, and says so
    c.cookies.clear()
    c.post("/api/session", json={"password": PASSWORD})
    assert c.get("/api/session").json()["via"] == "password"


@pytest.mark.parametrize("claims", [
    {"nonce": "someone-elses"},
    {"aud": "another-client"},
    {"iss": "http://evil.test"},
    {"exp": 1},
])
def test_a_bad_id_token_is_refused(paired, claims):
    fake, c = paired
    q = _start(c)
    fake.claims = {"nonce": q["nonce"], **claims}
    r = c.get("/auth/lucent/callback", params={"code": "abc", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?reason=lucent"
    assert not c.get("/api/session").json()["signed_in"]


def test_a_forged_signature_is_refused(paired):
    p = lucent.pairing()
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "aperture-abc", "iat": now, "exp": now + 60, "nonce": "n"}
    with pytest.raises(lucent.LucentError):
        lucent.verify_id_token(hs256(claims, secret="wrong"), p, "n")
    with pytest.raises(lucent.LucentError):
        lucent.verify_id_token(hs256(claims, alg="none"), p, "n")
    assert lucent.verify_id_token(hs256(claims), p, "n")["aud"] == "aperture-abc"


def test_a_callback_in_another_browser_is_refused(paired):
    fake, c = paired
    q = _start(c)
    fake.claims = {"nonce": q["nonce"]}
    other = TestClient(console_app)
    r = other.get("/auth/lucent/callback", params={"code": "abc", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?reason=lucent"
    # and the state is spent: the right browser cannot reuse it either
    r = c.get("/auth/lucent/callback", params={"code": "abc", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?reason=lucent"
