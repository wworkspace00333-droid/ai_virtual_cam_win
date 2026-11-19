"""0.6.0 Phase 9: remote control — off by default, localhost only, token (DPAPI), auth, rate limit, host / origin
checks, validated commands, WebSocket status, the mobile page."""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

import pytest

from vcstudio.app import remote as rm


def test_token_store_encrypts_and_renews(tmp_path):
    st = rm.TokenStore(tmp_path / "t.bin")
    a = st.load()
    assert len(a) >= 40 and st.load() == a
    raw = (tmp_path / "t.bin").read_bytes()
    if sys.platform == "win32":
        assert a.encode() not in raw                                     # DPAPI: not stored in clear text
    b = st.renew()
    assert b != a and st.load() == b
    (tmp_path / "t.bin").write_bytes(b"garbage")
    assert st.load() not in (a, b)                                         # unreadable: a new code


def test_limiter_bucket_and_lockout():
    lim = rm.Limiter()
    assert all(lim.allow("1.1.1.1", 0.0) for _ in range(int(rm.BURST)))
    assert not lim.allow("1.1.1.1", 0.0)
    assert lim.allow("1.1.1.1", 1.0)                                       # refilled
    for _ in range(rm.FAIL_LIMIT):
        lim.failed("2.2.2.2", 10.0)
    assert not lim.allow("2.2.2.2", 11.0) and lim.allow("2.2.2.2", 10.0 + rm.LOCKOUT_S + 1)


def test_websocket_frames_round_trip():
    assert rm.ws_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="     # RFC 6455 example
    assert rm.ws_frame("hi") == b"\x81\x02hi"
    assert rm.ws_frame("x" * 200)[:4] == b"\x81\x7e\x00\xc8"


@pytest.fixture
def app(paths):
    from vcstudio.app.application import AppContext
    os.environ.pop("VCS_NO_REMOTE", None)
    ctx = AppContext(paths, configure_logging=False)
    assert not ctx.remote.running                                          # off by default
    ctx.settings.set("remote", {"enabled": True, "port": 0, "lan": False})
    assert ctx.remote.running and ctx.remote.httpd.server_address[0] == "127.0.0.1"
    yield ctx
    ctx.shutdown()


def req(ctx, path, body=None, token=None, headers=None):
    url = f"http://127.0.0.1:{ctx.remote.port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_api_needs_the_token_and_runs_whitelisted_actions(app):
    tok = app.remote.token
    code, page = req(app, "/")
    assert code == 200 and b"Camera remote" in page and tok.encode() not in page
    assert req(app, "/api/status")[0] == 401
    assert req(app, "/api/status", token="wrong")[0] == 401
    code, body = req(app, "/api/status", token=tok)
    st = json.loads(body)
    assert code == 200 and st["scene"] == app.profiles.active_name and st["recording"] is False
    app.profiles.create("Talk")
    code, body = req(app, "/api/scene", {"name": "Talk"}, tok)
    assert code == 200 and app.profiles.active_name == "Talk"
    assert req(app, "/api/scene", {"name": "../etc"}, tok)[0] == 404
    code, body = req(app, "/api/action", {"type": "privacy", "level": "faces"}, tok)
    assert code == 200 and json.loads(body)["result"] == "privacy faces"
    assert req(app, "/api/action", {"type": "action", "id": "stop_processing"}, tok)[0] == 400   # not whitelisted
    assert req(app, "/api/action", {"type": "privacy", "level": {"x": 1}}, tok)[0] == 400
    assert req(app, "/api/action", ["not", "an", "object"], tok)[0] == 400
    assert req(app, "/nope", token=tok)[0] == 404


def test_host_origin_size_and_lockout(app):
    tok = app.remote.token
    assert req(app, "/api/status", token=tok, headers={"Host": "evil.example"})[0] == 421       # DNS rebinding
    assert req(app, "/api/action", {"type": "replay"}, tok, headers={"Origin": "http://evil.example"})[0] == 403
    big = {"type": "privacy", "level": "x" * 5000}
    assert req(app, "/api/action", big, tok)[0] == 413
    for _ in range(rm.FAIL_LIMIT):
        req(app, "/api/status", token="bad")
    assert req(app, "/api/status", token=tok)[0] == 429                    # locked out after failed logins


def _ws(ctx, token):
    s = socket.create_connection(("127.0.0.1", ctx.remote.port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{ctx.remote.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(1024)
    assert b"101" in resp.split(b"\r\n")[0] and rm.ws_accept(key).encode() in resp
    payload = json.dumps({"token": token}).encode()
    mask = os.urandom(4)
    s.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))
    return s


def _ws_msg(s):
    h = s.recv(2)
    n = h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", s.recv(2))[0]
    data = b""
    while len(data) < n:
        data += s.recv(n - len(data))
    return json.loads(data)


def test_websocket_live_status(app):
    s = _ws(app, app.remote.token)
    try:
        a = _ws_msg(s)
        b = _ws_msg(s)                                                     # one per second
        assert a["scene"] == app.profiles.active_name and "fps" in b
    finally:
        s.close()
    bad = _ws(app, "wrong")
    try:
        assert _ws_msg(bad) == {"error": "access code required"}
    finally:
        bad.close()


def test_renew_token_and_stop(app):
    old = app.remote.token
    new = app.remote.renew_token()
    assert new != old and req(app, "/api/status", token=old)[0] == 401
    assert req(app, "/api/status", token=new)[0] == 200
    app.settings.set("remote.enabled", False)
    assert not app.remote.running
    time.sleep(0.1)
