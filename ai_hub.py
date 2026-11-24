#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Hub - web app tren PC anh.
Em (Tinker) lam cua chinh: nhan viec cua anh, goi AI khac (ChatGPT truoc),
quan ly lich su + API key. Chay bang Python stdlib, khong can pip.
"""
import json
import os
import re
import secrets
import ssl
import stat as stat_module
import tempfile
import threading
import time
import tomllib
import uuid
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie, CookieError
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from mailbox_store import MessageStore, RoomConflict
import api_v1

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
HISTORY_PATH = os.path.join(BASE, "history.json")
PORT = int(os.environ.get("AI_HUB_PORT", "8090"))
HOST = os.environ.get("AI_HUB_HOST", "100.79.204.50")
MAILBOX = MessageStore(os.path.join(BASE, "messages.json"))

AI_DEFS = {
    "chatgpt": {"name": "ChatGPT", "api": "openai", "default_model": "gpt-6.1-sol", "enabled": True},
    "claude": {"name": "Claude", "api": "anthropic", "default_model": "claude-sonnet-4-5", "enabled": False},
    "gemini": {"name": "Gemini", "api": "google", "default_model": "gemini-2.0-flash", "enabled": False},
    "ollama": {"name": "Ollama (A100)", "api": "ollama", "default_model": "qwen3-coder:30b", "enabled": False},
}

_lock = threading.Lock()

# Key rotation is an internal, server-side operation. Tests replace these paths
# with temporary files; the live default is the Codex CLI config for this user.
CODEX_CONFIG_PATH = os.path.expanduser("~/.codex/config.toml")
KEY_ROTATION_AUDIT_PATH = os.path.join(BASE, "key_rotation_audit.jsonl")
_key_rotation_lock = threading.Lock()
XINGCHI_MODELS_URL = "https://api.xingchiapi.com/v1/models"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_xingchi_key(api_key):
    """Validate a candidate key only against the fixed models endpoint."""
    req = urllib.request.Request(
        XINGCHI_MODELS_URL,
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        with opener.open(req, timeout=15) as response:
            return (response.status == 200, "" if response.status == 200 else "upstream")
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return (False, "rejected" if status in (401, 403) else "upstream")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False, "upstream"


def _set_toml_string(text, table_path, key, value):
    """Replace one single-line TOML assignment while retaining other text."""
    table = ".".join(table_path)
    lines = text.splitlines(keepends=True)
    headers = []
    for index, line in enumerate(lines):
        raw = line.split("#", 1)[0].strip()
        if raw.startswith("["):
            headers.append((index, raw))
    matches = [i for i, header in headers if header == "[" + table + "]"]
    if len(matches) != 1:
        raise ValueError("target table missing or ambiguous")
    start = matches[0]
    end = next((i for i, _ in headers if i > start), len(lines))
    assignments = [i for i in range(start + 1, end)
                   if re.match(r"^\s*" + re.escape(key) + r"\s*=", lines[i])]
    if len(assignments) > 1:
        raise ValueError("target key ambiguous")
    encoded = json.dumps(value, ensure_ascii=True)
    if assignments:
        i = assignments[0]
        newline = "\r\n" if lines[i].endswith("\r\n") else "\n" if lines[i].endswith("\n") else ""
        comment = ""
        quote_char = None
        escaped = False
        for position, char in enumerate(lines[i]):
            if escaped:
                escaped = False
            elif char == "\\" and quote_char == '"':
                escaped = True
            elif quote_char and char == quote_char:
                quote_char = None
            elif not quote_char and char in {'"', "'"}:
                quote_char = char
            elif char == "#" and not quote_char:
                comment = " " + lines[i][position:].rstrip("\r\n")
                break
        lines[i] = "    " + key + " = " + encoded + comment + newline
    else:
        newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        insert_at = end
        if insert_at > start and lines[insert_at - 1].strip():
            lines.insert(insert_at, newline)
            insert_at += 1
        lines.insert(insert_at, "    " + key + " = " + encoded + newline)
    return "".join(lines)


def _file_bytes(path):
    with open(path, "rb") as stream:
        return stream.read()


def _write_atomic(path, content, mode, expected=None):
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(prefix=".aihub-key-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        if expected is not None and _file_bytes(path) != expected:
            raise RuntimeError("config changed during rotation")
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _create_config_backup(path, original, mode):
    directory = os.path.dirname(os.path.abspath(path))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path + ".bak." + stamp + "." + uuid.uuid4().hex[:8]
    fd, temp_path = tempfile.mkstemp(prefix=".aihub-backup-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        os.replace(temp_path, backup)
        return backup
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _audit_key_rotation(result, reason, key_length=None):
    event = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": "codex_key_rotation",
        "result": result,
        "reason": reason,
    }
    if key_length is not None:
        event["key_length"] = key_length
    try:
        with open(KEY_ROTATION_AUDIT_PATH, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")
    except OSError:
        # Audit data is deliberately metadata-only; never expose a key or error text.
        pass


def rotate_codex_key(api_key):
    """Verify, back up, then atomically replace the Codex provider key."""
    if (not isinstance(api_key, str) or not api_key or len(api_key) > 4096 or
            api_key != api_key.strip() or any(ord(char) < 33 or ord(char) > 126 for char in api_key)):
        return False, "invalid_request", None

    with _key_rotation_lock:
        valid, reason = verify_xingchi_key(api_key)
        if not valid:
            _audit_key_rotation("rejected", reason, len(api_key))
            return False, reason, None

        try:
            with open(CODEX_CONFIG_PATH, "rb") as stream:
                original = stream.read()
            stat = os.stat(CODEX_CONFIG_PATH)
            has_bom = original.startswith(b"\xef\xbb\xbf")
            text = original.decode("utf-8-sig")
            parsed = tomllib.loads(text)
            provider = parsed.get("model_providers", {}).get("OpenAI")
            if not isinstance(provider, dict):
                raise ValueError("target provider missing")
            if provider.get("experimental_bearer_token") == api_key:
                _audit_key_rotation("unchanged", "models_200", len(api_key))
                return True, "", None
            updated_text = _set_toml_string(
                text, ("model_providers", "OpenAI"), "experimental_bearer_token", api_key
            )
            updated_parsed = tomllib.loads(updated_text)
            if updated_parsed["model_providers"]["OpenAI"].get("experimental_bearer_token") != api_key:
                raise ValueError("updated key verification failed")
            updated = (b"\xef\xbb\xbf" if has_bom else b"") + updated_text.encode("utf-8")
            if os.stat(CODEX_CONFIG_PATH).st_mtime_ns != stat.st_mtime_ns or _file_bytes(CODEX_CONFIG_PATH) != original:
                raise RuntimeError("config changed during rotation")
            backup_mode = stat_module.S_IMODE(stat.st_mode) & 0o600
            if not backup_mode:
                backup_mode = 0o600
            backup = _create_config_backup(CODEX_CONFIG_PATH, original, backup_mode)
            if _file_bytes(CODEX_CONFIG_PATH) != original:
                raise RuntimeError("config changed during rotation")
            _write_atomic(CODEX_CONFIG_PATH, updated, stat.st_mode, expected=original)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError, RuntimeError):
            _audit_key_rotation("failed", "backup_or_write", len(api_key))
            return False, "write_failed", None

        _audit_key_rotation("applied", "models_200", len(api_key))
        return True, "", os.path.basename(backup)

# SSO-05: dang nhap 1 lan qua TinkerApp (ma dung 1 lan).
# AI Hub chay tren PC, khong goi thang 127.0.0.1:8095 duoc (TinkerApp chi
# bind 127.0.0.1 tren may 92) nen moi goi nguoc deu qua nginx HTTPS.
SSO_BASE_URL = "https://ubuntudev.tail8afec3.ts.net"
# CA bundle chi gom goc ISRG (X1 + X2) cua Let's Encrypt. Ly do (17/11/2026):
# cert Tailscale dung chuoi ECDSA moi (YE2 -> Root YE -> X2 -> X1); bo CA mac dinh
# (certifi) khien OpenSSL 3.0 chon duong cross-sign DST Root CA X3 da het han va bao
# "certificate has expired" du cert con han toi 13/02/2027. Chi dung 2 goc ISRG thi
# xac minh DUOC. Xac minh TLS VAN BAT (khong tat verify); chi thu hep tap goc tin cay
# ve dung CA cap cert cho TinkerApp. Goc X1 han 2035, X2 han 2040.
SSO_CA_BUNDLE = os.path.join(BASE, "isrg_roots.pem")
_sso_ctx = None


def _sso_ssl_context():
    global _sso_ctx
    if _sso_ctx is None:
        _sso_ctx = ssl.create_default_context(cafile=SSO_CA_BUNDLE)
    return _sso_ctx
TINKER_LOGIN_URL = "https://ubuntudev.tail8afec3.ts.net/"
DIRECT_URL = "http://100.79.204.50:8090"
SESSION_COOKIE = "aihub_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60
_sessions = {}
_session_lock = threading.Lock()
_last_revocation_pull = None


class _NoSSORedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _sso_request(path, payload=None, cookie=None):
    # Fixed server-to-server destination; never use Host/forwarded headers.
    # TLS verification stays ON (default context); the Tailscale cert is a
    # public Let's Encrypt cert the PC trusts. Never disable verification.
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(SSO_BASE_URL + path, data=data, headers=headers)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoSSORedirect(),
        urllib.request.HTTPSHandler(context=_sso_ssl_context()))
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError("SSO rejected")
            if payload is None:
                return None
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("SSO response too large")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ValueError("SSO rejected") from None


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def call_openai(api_key, model, messages, base_url="https://api.openai.com/v1"):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]


def list_models(api_key, base_url="https://api.openai.com/v1"):
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + api_key},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log query strings: they can contain a one-time SSO code.
        message = (fmt % args).split("?", 1)[0]
        print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message), flush=True)

    def _cookies(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            return cookies
        except CookieError:
            return SimpleCookie()

    def _authenticated(self):
        cookies = self._cookies()
        token = cookies.get(SESSION_COOKIE)
        now = time.time()
        with _session_lock:
            for key in list(_sessions):
                if _sessions[key]["expires_at"] <= now:
                    del _sessions[key]
            if token and token.value in _sessions:
                return True
        # The existing nginx proxy forwards the shared cookie. Validate it
        # centrally; cookie presence or forwarded headers alone prove nothing.
        central = cookies.get("tinker_session")
        if central:
            try:
                _sso_request("/internal/auth-check?module=ai-hub",
                             cookie="tinker_session=" + central.value)
                return True
            except (OSError, ValueError):
                pass
        return False

    def _login_page(self, message, status=401):
        parsed = urlparse(self.path)
        clean_query = urllib.parse.urlencode([
            (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if k != "sso_code"
        ])
        # Pin the origin instead of trusting an arbitrary Host header.
        target = DIRECT_URL + (parsed.path if parsed.path.startswith("/") else "/")
        if clean_query:
            target += "?" + clean_query
        login = TINKER_LOGIN_URL + "?" + urllib.parse.urlencode({"return_to": target})
        body = ("<!doctype html><html lang='vi'><meta charset='utf-8'>"
                "<title>Đăng nhập AI Hub</title><h1>Đăng nhập AI Hub</h1><p>"
                + escape(message) + "</p><a href='" + escape(login, quote=True)
                + "' target='_top'>Mở TinkerApp để lấy mã</a></html>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_session(self):
        if self._authenticated():
            return True
        if self.command == "GET" and urlparse(self.path).path in {"/", "/index.html", "/hub", "/hub.html"}:
            self._login_page("Mở TinkerApp để đăng nhập và lấy mã dùng một lần.")
        else:
            self._json({"ok": False, "error": "UNAUTHENTICATED"}, 401)
        return False

    def _require_internal(self):
        expected = os.environ.get("AI_HUB_INTERNAL_TOKEN", "")
        header = self.headers.get("Authorization", "")
        if not expected or not header.startswith("Bearer ") or not secrets.compare_digest(header[7:], expected):
            self._json({"ok": False, "error": "UNAUTHENTICATED"}, 401)
            return False
        return True

    def _receive_sso(self, codes):
        try:
            if len(codes) != 1 or not codes[0] or len(codes[0]) > 512:
                raise ValueError("Invalid code")
            result = _sso_request("/internal/sso-exchange",
                                  {"code": codes[0], "purpose": "ai-hub"})
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("Invalid response")
            user = result.get("user")
            central_id = result.get("central_session_id")
            if (not isinstance(user, dict) or type(user.get("id")) is not int
                    or not isinstance(user.get("username"), str)
                    or not isinstance(central_id, str) or not central_id):
                raise ValueError("Invalid session")
        except (OSError, ValueError):
            self._login_page("Mã không hợp lệ, đã hết hạn hoặc không thể đổi mã. "
                             "Quay lại TinkerApp để lấy mã mới.")
            return
        token = secrets.token_urlsafe(32)
        now = time.time()
        with _session_lock:
            _sessions[token] = {"user_id": user["id"], "username": user["username"],
                                "central_session_id": central_id, "created_at": now,
                                "expires_at": min(now + SESSION_MAX_AGE, datetime.fromisoformat(result["expires_at"]).timestamp()) if result.get("expires_at") else now + SESSION_MAX_AGE}
        # Relative root is safe even for malformed request paths and removes
        # the code before serving any page or subresources.
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", "%s=%s; HttpOnly; SameSite=Lax; Path=/; Max-Age=%d"
                         % (SESSION_COOKIE, token, SESSION_MAX_AGE))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self):
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/v1/"):
            api_v1.dispatch(self, "GET")
            return
        query = parse_qs(u.query, keep_blank_values=True)
        if "sso_code" in query:
            self._receive_sso(query["sso_code"])
            return
        if u.path == "/api/status":
            self._json({"ok": True, "mode": "room"})
            return
        if u.path == "/internal/messages":
            if not self._require_internal():
                return
            try:
                since = query.get("since", [None])[0]
                recipient = query.get("to", [None])[0]
                if u.path == "/internal/messages" and "wait" in query and recipient is None:
                    rows = MAILBOX.wait(since, min(20, max(0, int(query["wait"][0]))))
                else:
                    rows = MAILBOX.read(since, recipient)
                self._json({"ok": True, "messages": rows})
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except OSError:
                self._json({"ok": False, "error": "Không đọc được hộp thư."}, 500)
            return
        if not self._require_session():
            return
        if self.path == "/" or self.path.startswith("/index.html"):
            try:
                with open(os.path.join(BASE, "room.html"), "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                data = b"<h1>AI Hub</h1><p>Thieu index.html</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path in {"/hub", "/hub.html"}:
            try:
                with open(os.path.join(BASE, "hub.html"), "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                data = b"<h1>AI Hub</h1><p>Thieu hub.html</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/messages":
            try:
                rows = MAILBOX.read(query.get("since", [None])[0])
                self._json({"ok": True, "messages": rows})
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except OSError:
                self._json({"ok": False, "error": "Không đọc được hộp thư."}, 500)
            return
        if u.path in {"/api/history", "/api/models"}:
            self._json({"ok": False, "error": "AI chat đã tắt, dùng hộp thư."}, 410)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/v1/"):
            api_v1.dispatch(self, "POST")
            return
        if path == "/internal/rotate-codex-key":
            if not self._require_internal():
                return
            try:
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    raise ValueError("invalid request")
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 8192:
                    raise ValueError("invalid request")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"api_key"}:
                    raise ValueError("invalid request")
                ok, reason, backup = rotate_codex_key(payload["api_key"])
                if not ok:
                    status = 400 if reason == "invalid_request" else 502 if reason == "upstream" else 422 if reason == "rejected" else 500
                    self._json({"ok": False, "error": reason}, status)
                    return
                self._json({"ok": True, "changed": backup is not None,
                            "backup": backup, "key_length": len(payload["api_key"])}, 200)
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self._json({"ok": False, "error": "invalid_request"}, 400)
            return
        if path in {"/api/chat", "/api/key", "/api/clear"}:
            self._json({"ok": False, "error": "AI chat đã tắt, dùng hộp thư."}, 410)
            return
        if path in {"/internal/message", "/api/message"}:
            internal = path == "/internal/message"
            if not (self._require_internal() if internal else self._require_session()):
                return
            if not internal and self.headers.get("X-Mailbox-Request") != "1":
                self._json({"ok": False, "error": "Yêu cầu không hợp lệ."}, 403)
                return
            try:
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    raise ValueError("Yêu cầu JSON.")
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 100000:
                    raise ValueError("Nội dung quá lớn hoặc trống.")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Nội dung phải là JSON object.")
                sender = payload.get("from") if internal else "anh"
                if internal and (not isinstance(sender, str) or sender not in {"tinker", "emi"}):
                    raise ValueError("Đường nội bộ chỉ nhận Tinker hoặc Emi.")
                message = MAILBOX.append(sender, payload.get("to"), payload.get("body"),
                                         payload.get("ref"), payload.get("reply_to"),
                                         payload.get("kind") if internal else None)
                self._json({"ok": True, "message": message}, 201)
            except RoomConflict as exc:
                self._json({"ok": False, "error": str(exc)}, 409)
            except (ValueError, UnicodeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except OSError:
                self._json({"ok": False, "error": "Không ghi được hộp thư."}, 500)
            return
        # Handle internal logout endpoint BEFORE session check
        if self.path == "/internal/logout":
            # Check for internal token authentication
            auth_header = self.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                self._json({"ok": False, "error": "Yêu cầu token nội bộ"}, 401)
                return
            
            # Read internal token from environment or config (will be set by TinkerApp)
            import os
            internal_token = os.environ.get("AI_HUB_INTERNAL_TOKEN", "")
            if not internal_token:
                self._json({"ok": False, "error": "Token nội bộ chưa được cấu hình"}, 500)
                return
            
            if auth_header[7:] != internal_token:  # Skip "Bearer "
                self._json({"ok": False, "error": "Token nội bộ không hợp lệ"}, 401)
                return
                
            # Read and validate JSON body
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._json({"ok": False, "error": "Dữ liệu JSON không hợp lệ"}, 400)
                return
            
            central_session_id = payload.get("central_session_id")
            if not isinstance(central_session_id, str) or not central_session_id.strip():
                self._json({"ok": False, "error": "Thiếu central_session_id"}, 400)
                return
                
            # Delete matching local sessions
            with _session_lock:
                deleted_count = 0
                for token, session in list(_sessions.items()):
                    if session.get("central_session_id") == central_session_id:
                        del _sessions[token]
                        deleted_count += 1
                        
            self._json({"ok": True, "revoked": deleted_count})
            return
            
        if not self._require_session():
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if self.path == "/api/key":
            ai = payload.get("ai", "chatgpt")
            key = (payload.get("key") or "").strip()
            model = (payload.get("model") or "").strip()
            base_url = (payload.get("base_url") or "").strip()
            if ai not in AI_DEFS:
                self._json({"ok": False, "error": "ai khong hop le"}, 400)
                return
            if not key:
                self._json({"ok": False, "error": "thieu key"}, 400)
                return
            with _lock:
                cfg = load_json(CONFIG_PATH, {})
                cfg.setdefault(ai, {})["api_key"] = key
                if model:
                    cfg[ai]["model"] = model
                if base_url:
                    cfg[ai]["base_url"] = base_url
                save_json(CONFIG_PATH, cfg)
            self._json({"ok": True, "ai": ai, "model": cfg[ai].get("model", AI_DEFS[ai]["default_model"]),
                        "base_url": cfg[ai].get("base_url", "")})
            return
        if self.path == "/api/chat":
            ai = payload.get("ai", "chatgpt")
            message = (payload.get("message") or "").strip()
            # model: neu goi API truyen model thi dung model do (Sol chinh, Astra khi kho)
            # via: "user" (anh go tren UI) hoac "tinker" (em goi qua API) — de hien ro ai hoi
            req_model = (payload.get("model") or "").strip()
            via = (payload.get("via") or "user").strip() or "user"
            if ai not in AI_DEFS or not AI_DEFS[ai]["enabled"]:
                self._json({"ok": False, "error": "AI nay chua bat"}, 400)
                return
            if not message:
                self._json({"ok": False, "error": "thieu message"}, 400)
                return
            cfg = load_json(CONFIG_PATH, {})
            c = cfg.get(ai, {})
            api_key = c.get("api_key", "")
            if not api_key:
                self._json({"ok": False, "error": "chua nhap API key cho " + AI_DEFS[ai]["name"]}, 400)
                return
            model = req_model or c.get("model", AI_DEFS[ai]["default_model"])
            base_url = c.get("base_url", "https://api.openai.com/v1")
            with _lock:
                hist = load_json(HISTORY_PATH, {})
                msgs = hist.get(ai, [])
                # Chi gui 20 tin gan nhat lam context de nhe
                api_messages = [{"role": m["role"], "content": m["content"]} for m in msgs[-20:]]
                api_messages.append({"role": "user", "content": message})
            try:
                if ai == "chatgpt":
                    reply = call_openai(api_key, model, api_messages, base_url)
                else:
                    self._json({"ok": False, "error": "AI nay chua ho tro goi API"}, 400)
                    return
            except urllib.error.HTTPError as e:
                try:
                    err = json.load(e)
                    detail = err.get("error", {}).get("message", str(e))
                except Exception:
                    detail = str(e)
                self._json({"ok": False, "error": "API loi %s: %s" % (e.code, detail)}, 502)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 502)
                return
            with _lock:
                hist = load_json(HISTORY_PATH, {})
                msgs = hist.get(ai, [])
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                msgs.append({"role": "user", "content": message, "time": now, "via": via})
                msgs.append({"role": "assistant", "content": reply, "time": now, "model": model, "via": "ai"})
                hist[ai] = msgs[-200:]
                save_json(HISTORY_PATH, hist)
            self._json({"ok": True, "ai": ai, "model": model, "via": via, "reply": reply})
            return
        if self.path == "/api/clear":
            ai = payload.get("ai", "chatgpt")
            with _lock:
                hist = load_json(HISTORY_PATH, {})
                hist[ai] = []
                save_json(HISTORY_PATH, hist)
            self._json({"ok": True})
            return
        self._json({"ok": False, "error": "not found"}, 404)


# Background thread for revocation pull
def _start_revocation_pull():
    def pull_loop():
        global _last_revocation_pull
        import time
        while True:
            try:
                # Pull immediately on start, then every 5 minutes (300 seconds)
                import urllib.request
                import urllib.error

                if _last_revocation_pull:
                    from urllib.parse import quote
                    url = SSO_BASE_URL + "/internal/revocations?since=" + quote(_last_revocation_pull, safe="")
                else:
                    url = SSO_BASE_URL + "/internal/revocations"

                # Add authentication header
                auth_header = os.environ.get("AI_HUB_INTERNAL_TOKEN", "")
                if auth_header:
                    headers = {"Authorization": f"Bearer {auth_header}"}
                    req = urllib.request.Request(url, headers=headers)

                    _pull_opener = urllib.request.build_opener(
                        urllib.request.HTTPSHandler(context=_sso_ssl_context()))
                    with _pull_opener.open(req, timeout=5) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode("utf-8"))
                            revoked_list = data.get("revoked", [])

                            # Process revocations
                            if revoked_list:
                                with _session_lock:
                                    deleted_count = 0
                                    for item in revoked_list:
                                        central_session_id = item["central_session_id"]
                                        for token, session in list(_sessions.items()):
                                            if session.get("central_session_id") == central_session_id:
                                                del _sessions[token]
                                                deleted_count += 1

                                # Update last pull time
                                _last_revocation_pull = revoked_list[-1]["revoked_at"]

            except Exception as e:
                # Log error but continue loop
                print(f"Revocation pull failed: {e}")
                pass
            time.sleep(300)

    thread = threading.Thread(target=pull_loop, daemon=True)
    thread.start()

if __name__ == "__main__":
    _start_revocation_pull()
    print("AI Hub dang chay: http://%s:%d" % (HOST, PORT), flush=True)
    print("Mo tren trinh duyet: http://localhost:%d" % PORT, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
