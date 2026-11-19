"""Remote Control (0.6.0 Phase 9): a local HTTP + WebSocket API and a phone-sized web page.

Off by default (settings "remote": {"enabled": false, "port": 8799, "lan": false}).

Security model
  binding     127.0.0.1 only, unless the user switches on "lan" (the UI warns: anyone on the network who has the
              access code can control the camera); then 0.0.0.0
  token       32 random bytes (urlsafe base64), created once, stored encrypted with Windows DPAPI (CryptProtectData,
              current user) in <app data>/remote_token.bin; elsewhere a 0600 file. Every API call and the WebSocket
              must present it (Authorization: Bearer <token>, or the first WebSocket message {"token": ...});
              compared in constant time. "New code" in the UI replaces it (old clients stop working)
  host check  with localhost binding the Host header must be localhost / 127.0.0.1 (DNS rebinding); POST requests
              with an Origin header must come from the same host and port (cross-site requests from web pages)
  rate limit  token bucket per client address (10 req/s, burst 30); 5 failed logins per minute lock the address out
              for 60 s
  validation  JSON bodies up to 4 KB, known fields only; commands map to a fixed whitelist of actions run through
              AppContext.run_action (the same code as automation): no shell, no Python evaluation, no file paths,
              no file browsing; the only static content is the built-in page
Endpoints
  GET  /                 the mobile page (no data in it; it asks for the access code)
  GET  /api/status       app, camera, scene, recording, privacy
  GET  /api/scenes       scene names and slots
  POST /api/scene        {"name"}
  POST /api/action       {"type": privacy|record|replay|camera|layout|effects|color_preset, ...}
  GET  /ws               WebSocket: first message {"token"}; then a status message every second
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

MAX_BODY = 4096
RATE, BURST = 10.0, 30.0
FAIL_LIMIT, FAIL_WINDOW_S, LOCKOUT_S = 5, 60.0, 60.0
REMOTE_ACTIONS = ("privacy", "record", "replay", "camera", "layout", "effects", "color_preset")
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ----------------------------------------------------------------------------------------------- token storage
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out))   # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _windows(self) -> bool:
        return os.name == "nt"

    def load(self) -> str:
        """The stored token, creating one on first use."""
        try:
            raw = self.path.read_bytes()
            tok = (_dpapi(raw, False) if self._windows() else raw).decode("ascii")
            if len(tok) >= 32:
                return tok
        except (OSError, UnicodeDecodeError, ValueError):
            pass
        return self.renew()

    def renew(self) -> str:
        tok = secrets.token_urlsafe(32)
        data = tok.encode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(_dpapi(data, True) if self._windows() else data)
        if not self._windows():
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        return tok


# ----------------------------------------------------------------------------------------------- rate limiting
class Limiter:
    def __init__(self):
        self.buckets: dict[str, tuple[float, float]] = {}
        self.fails: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.locked.get(ip, 0) > now:
                return False
            tokens, last = self.buckets.get(ip, (BURST, now))
            tokens = min(BURST, tokens + (now - last) * RATE)
            if tokens < 1:
                self.buckets[ip] = (tokens, now)
                return False
            self.buckets[ip] = (tokens - 1, now)
            return True

    def failed(self, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW_S] + [now]
            self.fails[ip] = f
            if len(f) >= FAIL_LIMIT:
                self.locked[ip] = now + LOCKOUT_S
                self.fails[ip] = []
                log.warning("remote control: %s locked out for %.0f s after %d failed logins", ip, LOCKOUT_S,
                            FAIL_LIMIT)


# ----------------------------------------------------------------------------------------------- websocket frames
def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()


def ws_frame(text: str) -> bytes:
    data = text.encode("utf-8")
    n = len(data)
    head = bytes([0x81])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + data


def ws_read(sock: socket.socket, limit: int = MAX_BODY) -> tuple[int, bytes] | None:
    """(opcode, payload) of one client frame (clients must mask); None on close / error / oversize."""
    def exact(n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf
    h = exact(2)
    if h is None:
        return None
    op, masked, n = h[0] & 0x0F, h[1] & 0x80, h[1] & 0x7F
    if n == 126:
        b = exact(2)
        n = struct.unpack(">H", b)[0] if b else limit + 1
    elif n == 127:
        b = exact(8)
        n = struct.unpack(">Q", b)[0] if b else limit + 1
    if n > limit or not masked:
        return None
    mask = exact(4)
    payload = exact(n) if n else b""
    if mask is None or payload is None:
        return None
    return op, bytes(c ^ mask[i % 4] for i, c in enumerate(payload))


# ----------------------------------------------------------------------------------------------- server
def lan_address() -> str:
    """This PC's address on the local network, for the URL shown to the user (no packet is sent)."""
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


class RemoteServer:
    def __init__(self, ctx, token_path: Path):
        self.ctx = ctx
        self.tokens = TokenStore(token_path)
        self.token = ""
        self.limiter = Limiter()
        self.httpd: ThreadingHTTPServer | None = None
        self._t: threading.Thread | None = None
        self.port = 0
        self.lan = False
        self.error = ""
        self.requests = 0
        self.ws_clients = 0

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def apply_settings(self) -> None:
        cfg = self.ctx.settings.get("remote") or {}
        want = bool(cfg.get("enabled"))
        port = int(cfg.get("port") or 8799)
        lan = bool(cfg.get("lan"))
        if self.running and (not want or port != self.port or lan != self.lan):
            self.stop()
        if want and not self.running:
            self.start(port, lan)

    def start(self, port: int, lan: bool) -> None:
        self.token = self.tokens.load()
        host = "0.0.0.0" if lan else "127.0.0.1"
        handler = _make_handler(self)
        try:
            self.httpd = ThreadingHTTPServer((host, port), handler)
        except OSError as e:
            self.error = f"cannot listen on {host}:{port}: {e}"
            log.error("remote control: %s", self.error)
            self.httpd = None
            return
        self.httpd.daemon_threads = True
        self.port, self.lan, self.error = self.httpd.server_address[1], lan, ""
        self._t = threading.Thread(target=self.httpd.serve_forever, name="remote-control", daemon=True)
        self._t.start()
        log.info("remote control on %s:%d%s", host, self.port, " (LAN access ON)" if lan else "")
        if lan:
            self.ctx.db.log_event("WARNING", "remote", f"remote control open to the local network on port {self.port}")

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self._t = None

    def renew_token(self) -> str:
        self.token = self.tokens.renew()
        return self.token

    def check(self, presented: str) -> bool:
        return bool(self.token) and hmac.compare_digest(presented.encode(), self.token.encode())

    # ------------------------------------------------------------------ API
    def status(self) -> dict:
        ctx = self.ctx
        from vcstudio import __version__
        from vcstudio.replacement import privacy_mode as pm
        ps, cs = ctx.pipeline.stats(), ctx.capture.stats()
        return {"version": __version__, "scene": ctx.profiles.active_name, "capture": cs.state,
                "output": ps.state, "fps": round(ps.output_fps, 1), "camera": ps.camera_state,
                "recording": ctx.recording.recording, "replay": ctx.recording.replay is not None,
                "privacy": pm.level_of(ctx.profiles.active()["body"]), "effects": ctx.pipeline.replacement_enabled,
                "shield": bool(ctx.shield.cfg.enabled), "camera_source": ctx.multicam.override or "main",
                "cameras": ["main"] + ctx.multicam.sources.ids()}

    def scenes(self) -> list[dict]:
        return [{"name": n, "slot": self.ctx.scenes.slot_of(n)} for n in self.ctx.profiles.names()]

    def command(self, path: str, body: dict) -> tuple[int, dict]:
        if path == "/api/scene":
            name = body.get("name")
            if not isinstance(name, str) or name not in self.ctx.profiles.names():
                return 404, {"error": "no such scene"}
            return 200, {"result": self.ctx.run_action_main({"type": "scene", "name": name})}
        if path == "/api/action":
            t = body.get("type")
            if t not in REMOTE_ACTIONS:
                return 400, {"error": "unknown action"}
            allowed = {"privacy": ("level",), "record": ("do",), "replay": (), "camera": ("id",),
                       "layout": ("layout",), "effects": ("on",), "color_preset": ("name",)}[t]
            act = {"type": t}
            for k in allowed:
                v = body.get(k)
                if v is not None and not isinstance(v, (str, bool)):
                    return 400, {"error": f"bad value for {k}"}
                if v is not None:
                    act[k] = v[:64] if isinstance(v, str) else v
            return 200, {"result": self.ctx.run_action_main(act)}
        return 404, {"error": "not found"}


def _make_handler(srv: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vcstudio-remote"
        sys_version = ""

        def log_message(self, fmt, *args):            # requests are counted, not logged with their content
            pass

        # ---------------- helpers
        def _ip(self) -> str:
            return self.client_address[0]

        def _send(self, code: int, obj=None, ctype: str = "application/json", body: bytes | None = None) -> None:
            data = body if body is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; "
                                                            "style-src 'unsafe-inline'; connect-src 'self' ws: wss:")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            if srv.lan:
                return True
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", "[::1]")

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin.split("://", 1)[-1].lower() == (self.headers.get("Host") or "").lower()

        def _auth(self) -> bool:
            h = self.headers.get("Authorization") or ""
            ok = h.startswith("Bearer ") and srv.check(h[7:].strip())
            if not ok:
                srv.limiter.failed(self._ip())
            return ok

        def _gate(self) -> bool:
            srv.requests += 1
            if not srv.limiter.allow(self._ip()):
                self._send(429, {"error": "too many requests"})
                return False
            if not self._host_ok():
                self._send(421, {"error": "wrong host"})
                return False
            return True

        # ---------------- verbs
        def do_GET(self):  # noqa: N802
            if not self._gate():
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, ctype="text/html; charset=utf-8", body=PAGE.encode("utf-8"))
                return
            if path == "/ws" and (self.headers.get("Upgrade") or "").lower() == "websocket":
                self._websocket()
                return
            if path in ("/api/status", "/api/scenes"):
                if not self._auth():
                    self._send(401, {"error": "access code required"})
                    return
                self._send(200, srv.status() if path == "/api/status" else {"scenes": srv.scenes()})
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._gate():
                return
            if not self._origin_ok():
                self._send(403, {"error": "cross-site request refused"})
                return
            if not self._auth():
                self._send(401, {"error": "access code required"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 <= n <= MAX_BODY:
                self._send(413, {"error": "body too large"})
                return
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send(400, {"error": "JSON object expected"})
                return
            try:
                code, res = srv.command(self.path.split("?", 1)[0], body)
            except Exception as e:                       # noqa: BLE001 - never crash the server thread
                log.exception("remote command failed")
                code, res = 500, {"error": type(e).__name__}
            self._send(code, res)

        def _websocket(self) -> None:
            key = self.headers.get("Sec-WebSocket-Key")
            if not key or not self._origin_ok():
                self._send(400, {"error": "bad websocket request"})
                return
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws_accept(key))
            self.end_headers()
            sock = self.connection
            sock.settimeout(10)
            try:
                first = ws_read(sock)
                tok = ""
                if first and first[0] == 1:
                    try:
                        tok = str(json.loads(first[1]).get("token", ""))
                    except ValueError:
                        pass
                if not srv.check(tok):
                    srv.limiter.failed(self._ip())
                    sock.sendall(ws_frame(json.dumps({"error": "access code required"})))
                    return
                srv.ws_clients += 1
                sock.settimeout(1.0)
                while srv.running:
                    sock.sendall(ws_frame(json.dumps(srv.status())))
                    try:
                        msg = ws_read(sock)
                        if msg is None or msg[0] == 8:
                            break
                    except socket.timeout:
                        pass
            except OSError:
                pass
            finally:
                if srv.ws_clients:
                    srv.ws_clients -= 1
                self.close_connection = True

    return Handler


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Camera remote</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--fg:#e8eaf0;--muted:#9aa3b2;--acc:#4c8dff;--warn:#e5a50a;--bad:#e5484d}
@media (prefers-color-scheme: light){:root{--bg:#f4f5f7;--card:#fff;--fg:#14161a;--muted:#5d6675}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px system-ui,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 12px}.card{background:var(--card);border-radius:12px;padding:14px;margin-bottom:12px}
.muted{color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
button{font:inherit;padding:12px;border:0;border-radius:10px;background:#2a2f3a;color:#fff;cursor:pointer}
button.on{background:var(--acc)}button.rec{background:var(--bad)}input{font:inherit;padding:10px;border-radius:8px;
border:1px solid #444;width:100%;margin:8px 0}
</style></head><body>
<h1>Camera remote</h1>
<div class="card" id="login"><div class="muted">Enter the access code shown in the app (Studio → Remote Control).</div>
<input id="code" type="password" autocomplete="off"><button id="go">Connect</button><div id="err" class="muted"></div></div>
<div id="app" hidden>
<div class="card"><div id="st" class="muted">…</div></div>
<div class="card"><div class="muted">Scenes</div><div class="grid" id="scenes"></div></div>
<div class="card"><div class="muted">Controls</div><div class="grid">
<button id="rec">Record</button><button id="replay">Save replay</button><button id="priv">Privacy</button>
<button id="fx">Effects</button><button id="cam">Next camera</button></div></div>
</div>
<script>
let T=localStorage.getItem('vcs_code')||'',S={};
const $=id=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'Authorization':'Bearer '+T,
'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});if(r.status==401){logout('Wrong access code');throw 0}
return r.json()}
function logout(m){localStorage.removeItem('vcs_code');$('app').hidden=true;$('login').hidden=false;$('err').textContent=m||''}
function render(s){S=s;$('st').textContent=`Scene: ${s.scene} · ${s.output} · ${s.fps} fps · camera ${s.camera}`+
(s.recording?' · REC':'');$('rec').className=s.recording?'rec':'';$('rec').textContent=s.recording?'Stop recording':'Record';
$('priv').className=s.privacy!='off'?'on':'';$('priv').textContent='Privacy: '+s.privacy;$('fx').className=s.effects?'on':'';
document.querySelectorAll('#scenes button').forEach(b=>b.className=b.textContent==s.scene?'on':'')}
async function start(){try{const sc=await api('/api/scenes');$('scenes').innerHTML='';sc.scenes.forEach(x=>{
const b=document.createElement('button');b.textContent=x.name;b.onclick=()=>api('/api/scene',{name:x.name});
$('scenes').appendChild(b)});render(await api('/api/status'));$('login').hidden=true;$('app').hidden=false;live()}catch(e){}}
function live(){const ws=new WebSocket((location.protocol=='https:'?'wss://':'ws://')+location.host+'/ws');
ws.onopen=()=>ws.send(JSON.stringify({token:T}));ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.error){logout(m.error)}else render(m)};
ws.onclose=()=>setTimeout(()=>{if(!$('app').hidden)live()},2000)}
$('go').onclick=()=>{T=$('code').value.trim();localStorage.setItem('vcs_code',T);start()};
$('rec').onclick=()=>api('/api/action',{type:'record',do:S.recording?'stop':'start'});
$('replay').onclick=()=>api('/api/action',{type:'replay'});
$('priv').onclick=()=>api('/api/action',{type:'privacy',level:S.privacy=='off'?'faces':'off'});
$('fx').onclick=()=>api('/api/action',{type:'effects',on:!S.effects});
$('cam').onclick=()=>{const c=S.cameras||['main'];const i=(c.indexOf(S.camera_source)+1)%c.length;
api('/api/action',{type:'camera',id:c[i]})};
if(T)start();
</script></body></html>"""
