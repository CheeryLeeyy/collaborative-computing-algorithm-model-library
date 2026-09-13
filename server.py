#!/usr/bin/env python3
"""A dependency-free, LAN-ready file browser for algorithm directories."""

from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import http.cookies
import http.server
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import stat
import threading
import time
import traceback
import urllib.parse
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_DATA_ROOT = Path("/home/ly/jisuanjishu_docker/kt_1/unzips")
DEFAULT_USERNAME = "bupt"
# PBKDF2-SHA256 hash of the documented initial password. No plaintext is stored here.
DEFAULT_PASSWORD_HASH = (
    "pbkdf2_sha256$310000$algo-manager-default-v1$"
    "a8c60868a7790ab839fa4657d77f4d5806541b4957e84d1934926de07a909477"
)
COOKIE_NAME = "algorithm_manager_session"
TEMP_PREFIX = ".algorithm-upload-"
TEXT_EXTENSIONS = {
    ".txt", ".log", ".md", ".markdown", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".css", ".html", ".htm", ".json", ".jsonl", ".yaml",
    ".yml", ".xml", ".csv", ".tsv", ".ini", ".cfg", ".conf", ".toml",
    ".sh", ".bash", ".zsh", ".fish", ".sql", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".java", ".go", ".rs", ".dockerfile", ".gitignore",
    ".requirements", ".properties",
}
INLINE_MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
}
NATURAL_PARTS = re.compile(r"(\d+)")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
RANGE_HEADER = re.compile(r"bytes=(\d*)-(\d*)$")


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "request_error") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_size(value: str, default_unit: int = 1024**3) -> int:
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?i?b)?", text)
    if not match:
        raise ValueError(f"无法识别容量值: {value}")
    number = float(match.group(1))
    suffix = match.group(2) or ""
    units = {
        "": default_unit,
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    return int(number * units[suffix])


def make_password_hash(password: str, iterations: int = 310_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def parse_password_hash(encoded: str) -> tuple[int, bytes, bytes]:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        iterations = int(raw_iterations)
        salt = raw_salt.encode("ascii")
        digest = bytes.fromhex(raw_digest)
    except (AttributeError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("invalid password hash") from exc
    if (
        algorithm != "pbkdf2_sha256"
        or not 100_000 <= iterations <= 2_000_000
        or not 8 <= len(salt) <= 128
        or len(digest) != 32
        or CONTROL_CHARS.search(raw_salt)
    ):
        raise ValueError("invalid password hash")
    return iterations, salt, digest


def verify_password(password: str, encoded: str) -> bool:
    try:
        iterations, salt, expected = parse_password_hash(encoded)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in NATURAL_PARTS.split(value))


def clean_relative_path(raw: str | None, *, allow_root: bool = True) -> tuple[str, ...]:
    if raw is None or raw == "":
        if allow_root:
            return ()
        raise ApiError(400, "不能操作数据根目录", "root_forbidden")
    if len(raw) > 4096 or CONTROL_CHARS.search(raw):
        raise ApiError(400, "路径格式无效", "invalid_path")
    if "\\" in raw or raw.startswith("/"):
        raise ApiError(400, "只允许数据目录内的相对路径", "invalid_path")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ApiError(400, "只允许数据目录内的相对路径", "invalid_path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ApiError(400, "只允许数据目录内的相对路径", "invalid_path")
    return path.parts


def clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ApiError(400, "名称不能为空", "invalid_name")
    name = raw.strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or len(name.encode("utf-8")) > 255
        or CONTROL_CHARS.search(name)
    ):
        raise ApiError(400, "名称包含不允许的字符", "invalid_name")
    if name.startswith(TEMP_PREFIX):
        raise ApiError(400, "该名称为系统保留名称", "invalid_name")
    return name


def truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def collision_name(name: str, index: int) -> str:
    if index <= 0:
        return name
    marker = f" ({index})"
    stem, suffix = os.path.splitext(name)
    # Preserve a normal-sized extension; pathological extensions are treated as name text.
    if len(suffix.encode("utf-8")) > 64:
        stem, suffix = name, ""
    budget = 255 - len(marker.encode("utf-8")) - len(suffix.encode("utf-8"))
    shortened = truncate_utf8(stem, budget)
    if not shortened:
        shortened = "file"
        suffix = ""
    return f"{shortened}{marker}{suffix}"


def relative_text(parts: tuple[str, ...]) -> str:
    return "/".join(parts)


def open_directory_fd(root: Path, parts: tuple[str, ...]) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(root, flags)
    except OSError as exc:
        raise ApiError(500, "数据目录当前不可用", "data_root_unavailable") from exc
    try:
        for part in parts:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError as exc:
                raise ApiError(404, "目录不存在", "not_found") from exc
            except NotADirectoryError as exc:
                raise ApiError(400, "路径中包含非目录项", "not_a_directory") from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ApiError(403, "不允许访问符号链接目录", "symlink_forbidden") from exc
                raise ApiError(403, "无法访问该目录", "directory_unavailable") from exc
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def statvfs_bytes(fd: int) -> tuple[int, int, int]:
    values = os.fstatvfs(fd)
    total = values.f_blocks * values.f_frsize
    free = values.f_bavail * values.f_frsize
    used = total - values.f_bfree * values.f_frsize
    return total, used, free


def _decode_mount_field(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def storage_mount_for_path(path: Path) -> dict[str, str]:
    """Return the real filesystem backing a resolved path on Linux."""
    best: dict[str, str] = {"mount_point": str(path.anchor), "device": "", "filesystem": ""}
    best_length = 0
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                fields = line.rstrip("\n").split()
                separator = fields.index("-")
                mount_point = Path(_decode_mount_field(fields[4]))
                try:
                    path.relative_to(mount_point)
                except ValueError:
                    continue
                length = len(str(mount_point))
                if length >= best_length:
                    best_length = length
                    best = {
                        "mount_point": str(mount_point),
                        "filesystem": fields[separator + 1],
                        "device": _decode_mount_field(fields[separator + 2]),
                    }
    except (OSError, ValueError, IndexError):
        pass
    return best


def migrate_legacy_operation_log(legacy_path: Path, operation_path: Path) -> None:
    """Copy historical file/folder mutation entries into the dedicated operation log."""
    if operation_path.exists() or not legacy_path.is_file() or legacy_path == operation_path:
        return
    entries: list[str] = []
    try:
        with legacy_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("event") in {"upload", "delete", "mkdir"}:
                    entries.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        operation_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(operation_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(entries)
    except FileExistsError:
        return
    except OSError as exc:
        print(f"警告: 无法迁移旧操作日志: {exc}", flush=True)


@dataclass(frozen=True)
class AppConfig:
    data_root: Path
    static_root: Path
    username: str
    password_hash: str
    max_upload_bytes: int
    min_free_bytes: int
    preview_bytes: int
    docx_preview_bytes: int
    docx_unpacked_bytes: int
    docx_max_entries: int
    session_idle_seconds: int
    session_absolute_seconds: int
    cookie_secure: bool
    allow_recursive_delete: bool
    allowed_hosts: frozenset[str]
    operation_log: Path
    max_concurrent_uploads: int
    request_timeout_seconds: int


class SessionStore:
    def __init__(self, idle_seconds: int, absolute_seconds: int) -> None:
        self.idle_seconds = idle_seconds
        self.absolute_seconds = absolute_seconds
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> tuple[str, dict[str, Any]]:
        now = time.time()
        token = secrets.token_urlsafe(32)
        session = {
            "username": username,
            "csrf": secrets.token_urlsafe(32),
            "created": now,
            "last_seen": now,
        }
        with self._lock:
            self._prune(now)
            self._sessions[token] = session
        return token, session.copy()

    def get(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if (
                now - session["last_seen"] > self.idle_seconds
                or now - session["created"] > self.absolute_seconds
            ):
                self._sessions.pop(token, None)
                return None
            session["last_seen"] = now
            return session.copy()

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _prune(self, now: float) -> None:
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session["last_seen"] > self.idle_seconds
            or now - session["created"] > self.absolute_seconds
        ]
        for token in expired:
            self._sessions.pop(token, None)


class LoginLimiter:
    def __init__(self, attempts: int = 8, window_seconds: int = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_limited(self, address: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            bucket = self._failures[address]
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) < self.attempts:
                return False, 0
            return True, max(1, int(self.window_seconds - (now - bucket[0])))

    def fail(self, address: str) -> None:
        with self._lock:
            self._failures[address].append(time.time())

    def clear(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


class AlgorithmServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], config: AppConfig) -> None:
        self.config = config
        self.sessions = SessionStore(config.session_idle_seconds, config.session_absolute_seconds)
        self.login_limiter = LoginLimiter()
        self.upload_slots = threading.BoundedSemaphore(config.max_concurrent_uploads)
        self.operation_log_lock = threading.Lock()
        super().__init__(address, AlgorithmRequestHandler)

    def log_operation(
        self,
        event: str,
        client: str,
        result: str,
        *,
        username: str = "",
        path: str = "",
        detail: str = "",
    ) -> None:
        if event not in {"upload", "delete", "mkdir"}:
            return
        record = {
            "time": datetime.now().astimezone().isoformat(),
            "event": event,
            "result": result,
            "user": username,
            "client": client,
        }
        if path:
            record["path"] = path.replace("\r", "\\r").replace("\n", "\\n")
        if detail:
            record["detail"] = detail.replace("\r", " ").replace("\n", " ")[:300]
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self.operation_log_lock:
                self.config.operation_log.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(
                    self.config.operation_log,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                    0o600,
                )
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError:
            pass


class AlgorithmRequestHandler(http.server.BaseHTTPRequestHandler):
    server: AlgorithmServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.config.request_timeout_seconds)

    def handle_expect_100(self) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        if self.command == "POST" and parsed.path == "/api/upload":
            lengths = self.headers.get_all("Content-Length", [])
            try:
                length = int(lengths[0]) if len(lengths) == 1 else -1
            except ValueError:
                length = -1
            if length < 0 or length > self.server.config.max_upload_bytes:
                self.close_connection = True
                self._send_json(
                    413,
                    {"error": "文件超过服务器允许的单文件大小", "code": "file_too_large"},
                )
                return False
        return super().handle_expect_100()

    def do_GET(self) -> None:
        self._run_request("GET")

    def do_HEAD(self) -> None:
        self._run_request("HEAD")

    def do_POST(self) -> None:
        self._run_request("POST")

    def do_OPTIONS(self) -> None:
        self.send_response(405)
        self._common_headers("api")
        self.send_header("Allow", "GET, HEAD, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _run_request(self, method: str) -> None:
        try:
            self._validate_host()
            if method == "POST":
                self._validate_origin()
            self._dispatch(method)
        except ApiError as exc:
            if method == "POST":
                # A rejected upload may still have an unread multi-GB body. Closing the
                # connection prevents those bytes being parsed as the next HTTP request.
                self.close_connection = True
            self._send_json(exc.status, {"error": exc.message, "code": exc.code})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc()
            try:
                if method == "POST":
                    self.close_connection = True
                self._send_json(500, {"error": "服务器处理请求时发生错误", "code": "internal_error"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if method in {"GET", "HEAD"} and path in {"/", "/index.html"}:
            self._serve_static("index.html", method == "HEAD")
            return
        if method in {"GET", "HEAD"} and path in {
            "/static/app.css",
            "/static/app.js",
            "/static/vendor/jszip-3.10.1/dist/jszip.min.js",
            "/static/vendor/docx-preview-0.4.0/dist/docx-preview.min.js",
        }:
            self._serve_static(path.removeprefix("/static/"), method == "HEAD")
            return
        if method == "GET" and path == "/api/session":
            self._api_session()
            return
        if method == "POST" and path == "/api/login":
            self._api_login()
            return
        if method == "POST" and path == "/api/logout":
            self._api_logout()
            return
        if method == "GET" and path == "/api/system":
            self._api_system()
            return
        if method == "GET" and path == "/api/list":
            self._api_list(query)
            return
        if method == "GET" and path == "/api/preview":
            self._api_preview(query)
            return
        if method == "GET" and path == "/api/docx-info":
            self._api_docx_info(query)
            return
        if method in {"GET", "HEAD"} and path == "/api/docx-file":
            self._api_docx_file(query, method == "HEAD")
            return
        if method in {"GET", "HEAD"} and path == "/api/file":
            self._api_file(query, method == "HEAD")
            return
        if method == "POST" and path == "/api/upload":
            self._api_upload(query)
            return
        if method == "POST" and path == "/api/mkdir":
            self._api_mkdir()
            return
        if method == "POST" and path == "/api/delete":
            self._api_delete()
            return
        if path.startswith("/api/"):
            raise ApiError(404, "接口不存在", "not_found")
        raise ApiError(404, "页面不存在", "not_found")

    def _common_headers(self, kind: str = "api") -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")
        if kind == "page":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' blob: data:; media-src 'self' blob:; "
                "font-src 'self' blob: data:; "
                "frame-src 'self'; object-src 'none'; connect-src 'self'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'self'",
            )
        elif kind == "file":
            self.send_header(
                "Content-Security-Policy",
                "sandbox; default-src 'none'; frame-ancestors 'self'",
            )
        else:
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")

    def _send_json(
        self,
        status_code: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self._common_headers("api")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_static(self, name: str, head_only: bool) -> None:
        if name not in {
            "index.html",
            "app.css",
            "app.js",
            "vendor/jszip-3.10.1/dist/jszip.min.js",
            "vendor/docx-preview-0.4.0/dist/docx-preview.min.js",
        }:
            raise ApiError(404, "文件不存在", "not_found")
        path = self.server.config.static_root / name
        try:
            body = path.read_bytes()
        except FileNotFoundError as exc:
            raise ApiError(500, "页面资源缺失", "static_missing") from exc
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }[path.suffix]
        self.send_response(200)
        self._common_headers("page")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _validate_host(self) -> None:
        raw_host = self.headers.get("Host", "")
        if not raw_host or len(raw_host) > 255 or CONTROL_CHARS.search(raw_host):
            raise ApiError(400, "Host 请求头无效", "invalid_host")
        try:
            parsed = urllib.parse.urlsplit(f"//{raw_host}")
            hostname = (parsed.hostname or "").rstrip(".").casefold()
            _ = parsed.port
        except ValueError as exc:
            raise ApiError(400, "Host 请求头无效", "invalid_host") from exc
        configured = self.server.config.allowed_hosts
        if configured:
            if hostname not in configured:
                raise ApiError(400, "该访问地址未被允许", "host_not_allowed")
            return
        local_names = {
            "localhost",
            socket.gethostname().rstrip(".").casefold(),
            socket.getfqdn().rstrip(".").casefold(),
        }
        if hostname in local_names:
            return
        try:
            ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ApiError(400, "请使用服务器 IP 地址访问", "host_not_allowed") from exc

    def _validate_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != self.headers.get("Host", "").casefold():
            raise ApiError(403, "请求来源校验失败", "origin_mismatch")

    def _query_value(
        self, query: dict[str, list[str]], name: str, default: str = ""
    ) -> str:
        values = query.get(name)
        return values[0] if values else default

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            cookie = http.cookies.SimpleCookie()
            cookie.load(raw)
            morsel = cookie.get(COOKIE_NAME)
            return morsel.value if morsel else None
        except http.cookies.CookieError:
            return None

    def _require_session(self, *, csrf: bool = False) -> tuple[str, dict[str, Any]]:
        token = self._cookie_token()
        session = self.server.sessions.get(token)
        if not session:
            raise ApiError(401, "会话已过期，请重新登录", "authentication_required")
        if csrf:
            supplied = self.headers.get("X-CSRF-Token", "")
            if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
                raise ApiError(403, "安全校验失败，请刷新页面后重试", "csrf_failed")
        return token or "", session

    def _read_json(self, max_bytes: int = 65_536) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "不支持 Transfer-Encoding 请求体", "unsupported_transfer_encoding")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise ApiError(411, "请求缺少长度信息", "length_required")
        raw_length = lengths[0]
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "请求长度无效", "invalid_length") from exc
        if length < 0 or length > max_bytes:
            raise ApiError(413, "请求内容过大", "request_too_large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(400, "请求内容不完整", "incomplete_request")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "JSON 请求格式无效", "invalid_json") from exc
        if not isinstance(value, dict):
            raise ApiError(400, "JSON 请求必须是对象", "invalid_json")
        return value

    def _consume_optional_body(self, max_bytes: int = 4096) -> None:
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "不支持 Transfer-Encoding 请求体", "unsupported_transfer_encoding")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            return
        if len(lengths) != 1:
            raise ApiError(400, "Content-Length 请求头无效", "invalid_length")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise ApiError(400, "请求长度无效", "invalid_length") from exc
        if length < 0 or length > max_bytes:
            raise ApiError(413, "请求内容过大", "request_too_large")
        if length and len(self.rfile.read(length)) != length:
            raise ApiError(400, "请求内容不完整", "incomplete_request")

    def _api_login(self) -> None:
        address = self.client_address[0]
        limited, retry_after = self.server.login_limiter.is_limited(address)
        if limited:
            self.close_connection = True
            self._send_json(
                429,
                {"error": "登录尝试过多，请稍后再试", "code": "rate_limited"},
                {"Retry-After": str(retry_after)},
            )
            return
        data = self._read_json()
        username = data.get("username")
        password = data.get("password")
        valid_user = isinstance(username, str) and len(username) <= 256 and hmac.compare_digest(
            username.encode("utf-8"), self.server.config.username.encode("utf-8")
        )
        valid_password = isinstance(password, str) and len(password) <= 4096 and verify_password(
            password, self.server.config.password_hash
        )
        if not (valid_user and valid_password):
            self.server.login_limiter.fail(address)
            raise ApiError(401, "用户名或密码错误", "invalid_credentials")

        old_token = self._cookie_token()
        self.server.sessions.delete(old_token)
        token, session = self.server.sessions.create(self.server.config.username)
        self.server.login_limiter.clear(address)
        cookie = (
            f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={self.server.config.session_absolute_seconds}"
        )
        if self.server.config.cookie_secure:
            cookie += "; Secure"
        self._send_json(
            200,
            {
                "authenticated": True,
                "username": session["username"],
                "csrf": session["csrf"],
            },
            {"Set-Cookie": cookie},
        )

    def _api_logout(self) -> None:
        token, _ = self._require_session(csrf=True)
        self._consume_optional_body()
        self.server.sessions.delete(token)
        cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if self.server.config.cookie_secure:
            cookie += "; Secure"
        self._send_json(200, {"ok": True}, {"Set-Cookie": cookie})

    def _api_session(self) -> None:
        _, session = self._require_session()
        self._send_json(
            200,
            {
                "authenticated": True,
                "username": session["username"],
                "csrf": session["csrf"],
            },
        )

    def _api_system(self) -> None:
        self._require_session()
        fd = open_directory_fd(self.server.config.data_root, ())
        try:
            total, used, free = statvfs_bytes(fd)
        finally:
            os.close(fd)
        storage = storage_mount_for_path(self.server.config.data_root)
        self._send_json(
            200,
            {
                "root_name": self.server.config.data_root.name,
                "disk": {"total": total, "used": used, "free": free},
                "storage": storage,
                "max_upload_bytes": self.server.config.max_upload_bytes,
                "min_free_bytes": self.server.config.min_free_bytes,
                "preview_bytes": self.server.config.preview_bytes,
                "docx_preview_bytes": self.server.config.docx_preview_bytes,
                "docx_unpacked_bytes": self.server.config.docx_unpacked_bytes,
                "docx_max_entries": self.server.config.docx_max_entries,
                "recursive_delete": self.server.config.allow_recursive_delete,
            },
        )

    def _api_list(self, query: dict[str, list[str]]) -> None:
        self._require_session()
        parts = clean_relative_path(self._query_value(query, "path"))
        search = self._query_value(query, "search").strip().casefold()
        dirs_only = self._query_value(query, "dirs_only") == "1"
        try:
            page = max(1, int(self._query_value(query, "page", "1")))
            per_page = min(200, max(20, int(self._query_value(query, "per_page", "100"))))
        except ValueError as exc:
            raise ApiError(400, "分页参数无效", "invalid_pagination") from exc

        fd = open_directory_fd(self.server.config.data_root, parts)
        entries: list[dict[str, Any]] = []
        try:
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    if entry.name.startswith(TEMP_PREFIX):
                        continue
                    try:
                        entry.name.encode("utf-8")
                    except UnicodeEncodeError:
                        # Browsers cannot address surrogate-escaped filesystem names safely.
                        continue
                    if search and search not in entry.name.casefold():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        is_link = stat.S_ISLNK(info.st_mode)
                        is_dir = stat.S_ISDIR(info.st_mode) and not is_link
                        is_file = stat.S_ISREG(info.st_mode) and not is_link
                        if dirs_only and not is_dir:
                            continue
                        if is_dir:
                            kind = "directory"
                        elif is_file:
                            kind = "file"
                        elif is_link:
                            kind = "symlink"
                        else:
                            kind = "other"
                        child_parts = parts + (entry.name,)
                        mime = ""
                        if is_file:
                            mime = mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
                        entries.append(
                            {
                                "name": entry.name,
                                "path": relative_text(child_parts),
                                "kind": kind,
                                "size": info.st_size if is_file else None,
                                "modified": datetime.fromtimestamp(
                                    info.st_mtime, timezone.utc
                                ).isoformat(),
                                "mime": mime,
                            }
                        )
                    except (FileNotFoundError, PermissionError, OSError):
                        continue
        finally:
            os.close(fd)

        entries.sort(
            key=lambda item: (
                0 if item["kind"] == "directory" else 1,
                natural_key(item["name"]),
            )
        )
        total = len(entries)
        pages = max(1, (total + per_page - 1) // per_page)
        if page > pages:
            page = pages
        start = (page - 1) * per_page
        self._send_json(
            200,
            {
                "path": relative_text(parts),
                "entries": entries[start : start + per_page],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": pages,
                },
            },
        )

    def _open_regular_file(self, parts: tuple[str, ...]) -> tuple[int, os.stat_result]:
        if not parts:
            raise ApiError(400, "请选择文件", "file_required")
        parent_fd = open_directory_fd(self.server.config.data_root, parts[:-1])
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            try:
                file_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
            except FileNotFoundError as exc:
                raise ApiError(404, "文件不存在", "not_found") from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ApiError(403, "不允许访问符号链接", "symlink_forbidden") from exc
                raise ApiError(403, "无法打开该文件", "file_unavailable") from exc
        finally:
            os.close(parent_fd)
        try:
            info = os.fstat(file_fd)
        except Exception:
            os.close(file_fd)
            raise
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise ApiError(400, "只能预览或下载普通文件", "not_regular_file")
        return file_fd, info

    def _api_preview(self, query: dict[str, list[str]]) -> None:
        self._require_session()
        parts = clean_relative_path(self._query_value(query, "path"), allow_root=False)
        name = parts[-1]
        suffix = Path(name).suffix.casefold()
        lower_name = name.casefold()
        is_text = (
            suffix in TEXT_EXTENSIONS
            or lower_name in {"dockerfile", "makefile", "readme", "license", "requirements.txt"}
        )
        if not is_text:
            guessed = mimetypes.guess_type(name)[0] or ""
            is_text = guessed.startswith("text/") and suffix not in {".svg"}
        if not is_text:
            raise ApiError(415, "此文件格式不支持文本预览", "preview_unsupported")
        file_fd, info = self._open_regular_file(parts)
        try:
            limit = self.server.config.preview_bytes
            raw = os.read(file_fd, limit + 1)
        finally:
            os.close(file_fd)
        if b"\x00" in raw[:8192]:
            raise ApiError(415, "该文件是二进制文件，不能按文本预览", "binary_file")
        truncated = len(raw) > self.server.config.preview_bytes
        raw = raw[: self.server.config.preview_bytes]
        encoding = "utf-8"
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
                encoding = "gb18030"
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
                encoding = "utf-8（含替换字符）"
        self._send_json(
            200,
            {
                "path": relative_text(parts),
                "content": text,
                "size": info.st_size,
                "truncated": truncated,
                "encoding": encoding,
            },
        )

    def _api_docx_info(self, query: dict[str, list[str]]) -> None:
        self._require_session()
        parts = clean_relative_path(self._query_value(query, "path"), allow_root=False)
        if Path(parts[-1]).suffix.casefold() != ".docx":
            raise ApiError(415, "只能使用 DOCX 在线预览", "docx_required")
        file_fd, info = self._open_regular_file(parts)
        try:
            document_info = self._inspect_docx(file_fd, info)
        finally:
            os.close(file_fd)
        self._send_json(
            200,
            {
                "ok": True,
                "path": relative_text(parts),
                "size": info.st_size,
                **document_info,
            },
        )

    def _inspect_docx(self, file_fd: int, info: os.stat_result) -> dict[str, int]:
        if info.st_size > self.server.config.docx_preview_bytes:
            raise ApiError(
                413,
                f"DOCX 超过在线预览上限（{self.server.config.docx_preview_bytes} 字节）",
                "docx_too_large",
            )
        try:
            with os.fdopen(os.dup(file_fd), "rb") as source:
                with zipfile.ZipFile(source) as archive:
                    entries = archive.infolist()
                    if len(entries) > self.server.config.docx_max_entries:
                        raise ApiError(413, "DOCX 内部文件数量过多", "docx_too_many_entries")
                    total_unpacked = 0
                    names: set[str] = set()
                    for entry in entries:
                        archive_path = PurePosixPath(entry.filename)
                        if (
                            archive_path.is_absolute()
                            or "\\" in entry.filename
                            or any(part == ".." for part in archive_path.parts)
                        ):
                            raise ApiError(415, "DOCX 包含不安全的内部路径", "invalid_docx")
                        if entry.flag_bits & 0x1:
                            raise ApiError(415, "不支持加密的 DOCX", "encrypted_docx")
                        total_unpacked += entry.file_size
                        if total_unpacked > self.server.config.docx_unpacked_bytes:
                            raise ApiError(
                                413,
                                "DOCX 解压后体积过大，不能在线预览",
                                "docx_unpacked_too_large",
                            )
                        names.add(entry.filename)
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ApiError(415, "文件不是有效的 DOCX", "invalid_docx") from exc
        finally:
            os.lseek(file_fd, 0, os.SEEK_SET)
        if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
            raise ApiError(415, "DOCX 缺少必要的文档结构", "invalid_docx")
        return {"unpacked_size": total_unpacked, "entries": len(entries)}

    def _api_docx_file(self, query: dict[str, list[str]], head_only: bool) -> None:
        self._require_session()
        parts = clean_relative_path(self._query_value(query, "path"), allow_root=False)
        if Path(parts[-1]).suffix.casefold() != ".docx":
            raise ApiError(415, "只能使用 DOCX 在线预览", "docx_required")
        file_fd, info = self._open_regular_file(parts)
        try:
            self._inspect_docx(file_fd, info)
            self.send_response(200)
            self._common_headers("file")
            self.send_header(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.send_header("Content-Length", str(info.st_size))
            self.send_header("Content-Disposition", self._content_disposition(parts[-1], False))
            self.end_headers()
            if head_only:
                return
            remaining = info.st_size
            while remaining:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    self.close_connection = True
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            os.close(file_fd)

    def _content_disposition(self, name: str, inline: bool) -> str:
        safe_ascii = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "download"
        encoded = urllib.parse.quote(name, safe="")
        disposition = "inline" if inline else "attachment"
        return f"{disposition}; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"

    def _api_file(self, query: dict[str, list[str]], head_only: bool) -> None:
        self._require_session()
        parts = clean_relative_path(self._query_value(query, "path"), allow_root=False)
        download = self._query_value(query, "download") == "1"
        file_fd, info = self._open_regular_file(parts)
        try:
            name = parts[-1]
            suffix = Path(name).suffix.casefold()
            inline = not download and suffix in INLINE_MIME_BY_EXTENSION
            content_type = INLINE_MIME_BY_EXTENSION[suffix] if inline else "application/octet-stream"
            start = 0
            end = info.st_size - 1
            status_code = 200
            range_value = self.headers.get("Range")
            if range_value:
                match = RANGE_HEADER.fullmatch(range_value.strip()) if len(range_value) <= 128 else None
                if not match or "," in range_value or info.st_size == 0:
                    self._range_not_satisfiable(info.st_size)
                    return
                first, last = match.groups()
                try:
                    if first:
                        start = int(first)
                        end = int(last) if last else end
                    else:
                        suffix_length = int(last or "0")
                        if suffix_length <= 0:
                            self._range_not_satisfiable(info.st_size)
                            return
                        start = max(0, info.st_size - suffix_length)
                except ValueError:
                    self._range_not_satisfiable(info.st_size)
                    return
                if start >= info.st_size or end < start:
                    self._range_not_satisfiable(info.st_size)
                    return
                end = min(end, info.st_size - 1)
                status_code = 206
            length = max(0, end - start + 1) if info.st_size else 0
            self.send_response(status_code)
            self._common_headers("file")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", self._content_disposition(name, inline))
            if status_code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{info.st_size}")
            self.end_headers()
            if head_only or length == 0:
                return
            os.lseek(file_fd, start, os.SEEK_SET)
            remaining = length
            while remaining:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            os.close(file_fd)

    def _range_not_satisfiable(self, size: int) -> None:
        self.send_response(416)
        self._common_headers("file")
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _api_upload(self, query: dict[str, list[str]]) -> None:
        _, session = self._require_session(csrf=True)
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "上传必须提供 Content-Length", "length_required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise ApiError(411, "上传必须提供一个 Content-Length", "length_required")
        raw_length = lengths[0]
        try:
            length = int(raw_length or "")
        except ValueError as exc:
            raise ApiError(411, "上传必须提供有效的 Content-Length", "length_required") from exc
        if length < 0 or length > self.server.config.max_upload_bytes:
            raise ApiError(413, "文件超过服务器允许的单文件大小", "file_too_large")
        parts = clean_relative_path(self._query_value(query, "path"))
        name = clean_name(self._query_value(query, "filename"))
        conflict = self._query_value(query, "conflict", "rename")
        if conflict not in {"error", "rename", "overwrite"}:
            raise ApiError(400, "重名处理方式无效", "invalid_conflict_mode")
        if not self.server.upload_slots.acquire(blocking=False):
            raise ApiError(429, "当前上传任务较多，请稍后重试", "too_many_uploads")
        parent_fd = -1
        file_fd = -1
        temp_name = ""
        final_name = name
        committed = False
        path_for_audit = relative_text(parts + (name,))
        try:
            parent_fd = open_directory_fd(self.server.config.data_root, parts)
            _, _, free = statvfs_bytes(parent_fd)
            if length + self.server.config.min_free_bytes > free:
                raise ApiError(507, "磁盘剩余空间不足，无法上传此文件", "insufficient_storage")
            flags = os.O_WRONLY | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            if conflict == "error":
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ApiError(409, "同名文件已存在", "file_exists")

            temp_name = f"{TEMP_PREFIX}{secrets.token_hex(12)}.part"
            file_fd = os.open(temp_name, flags | os.O_EXCL, 0o640, dir_fd=parent_fd)

            remaining = length
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ApiError(400, "上传中断，未收到完整文件", "incomplete_upload")
                view = memoryview(chunk)
                while view:
                    written = os.write(file_fd, view)
                    view = view[written:]
                remaining -= len(chunk)
            os.fsync(file_fd)
            os.close(file_fd)
            file_fd = -1
            if conflict == "overwrite":
                try:
                    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISREG(current.st_mode):
                        raise ApiError(
                            409,
                            "同名项目不是普通文件，不能覆盖",
                            "unsafe_overwrite_target",
                        )
                except FileNotFoundError:
                    pass
                os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                temp_name = ""
                final_name = name
                committed = True
            else:
                for index in range(0, 10_000):
                    candidate = collision_name(name, index)
                    try:
                        os.link(
                            temp_name,
                            candidate,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                        final_name = candidate
                        committed = True
                        os.unlink(temp_name, dir_fd=parent_fd)
                        temp_name = ""
                        break
                    except FileExistsError:
                        if conflict == "error":
                            raise ApiError(409, "同名文件已存在", "file_exists")
                if not committed:
                    raise ApiError(409, "无法生成不重名的文件名", "file_exists")
            result_path = relative_text(parts + (final_name,))
            self.server.log_operation(
                "upload",
                self.client_address[0],
                "success",
                username=session["username"],
                path=result_path,
                detail=f"bytes={length}; conflict={conflict}",
            )
            self._send_json(
                201,
                {
                    "ok": True,
                    "name": final_name,
                    "path": result_path,
                    "size": length,
                    "renamed": final_name != name,
                },
            )
        except Exception as exc:
            self.server.log_operation(
                "upload",
                self.client_address[0],
                "failed",
                username=session["username"],
                path=path_for_audit,
                detail=exc.message if isinstance(exc, ApiError) else type(exc).__name__,
            )
            if file_fd >= 0:
                os.close(file_fd)
                file_fd = -1
            if parent_fd >= 0 and temp_name:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            raise
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            if parent_fd >= 0:
                os.close(parent_fd)
            self.server.upload_slots.release()

    def _api_mkdir(self) -> None:
        _, session = self._require_session(csrf=True)
        data = self._read_json()
        parts = clean_relative_path(data.get("path") if isinstance(data.get("path"), str) else "")
        name = clean_name(data.get("name"))
        result_path = relative_text(parts + (name,))
        parent_fd = -1
        try:
            parent_fd = open_directory_fd(self.server.config.data_root, parts)
            try:
                os.mkdir(name, 0o750, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise ApiError(409, "同名文件或文件夹已存在", "already_exists") from exc
        except Exception as exc:
            self.server.log_operation(
                "mkdir",
                self.client_address[0],
                "failed",
                username=session["username"],
                path=result_path,
                detail=exc.message if isinstance(exc, ApiError) else type(exc).__name__,
            )
            raise
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
        self.server.log_operation(
            "mkdir",
            self.client_address[0],
            "success",
            username=session["username"],
            path=result_path,
            detail="kind=directory",
        )
        self._send_json(201, {"ok": True, "name": name, "path": result_path})

    def _remove_tree_at(self, parent_fd: int, name: str) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        child_fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            with os.scandir(child_fd) as iterator:
                children = list(iterator)
            for entry in children:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                    self._remove_tree_at(child_fd, entry.name)
                else:
                    os.unlink(entry.name, dir_fd=child_fd)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)

    def _api_delete(self) -> None:
        _, session = self._require_session(csrf=True)
        data = self._read_json()
        raw_path = data.get("path") if isinstance(data.get("path"), str) else None
        parts = clean_relative_path(raw_path, allow_root=False)
        path_text = relative_text(parts)
        parent_fd = -1
        kind = ""
        item_size = 0
        try:
            parent_fd = open_directory_fd(self.server.config.data_root, parts[:-1])
            try:
                info = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ApiError(404, "要删除的项目不存在", "not_found") from exc
            item_size = info.st_size
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                kind = "directory"
                if self.server.config.allow_recursive_delete:
                    self._remove_tree_at(parent_fd, parts[-1])
                else:
                    try:
                        os.rmdir(parts[-1], dir_fd=parent_fd)
                    except OSError as exc:
                        if exc.errno == errno.ENOTEMPTY:
                            raise ApiError(
                                409,
                                "默认安全设置只允许删除空文件夹",
                                "directory_not_empty",
                            ) from exc
                        raise
            else:
                kind = "file" if stat.S_ISREG(info.st_mode) else "link_or_other"
                os.unlink(parts[-1], dir_fd=parent_fd)
        except Exception as exc:
            self.server.log_operation(
                "delete",
                self.client_address[0],
                "failed",
                username=session["username"],
                path=path_text,
                detail=exc.message if isinstance(exc, ApiError) else type(exc).__name__,
            )
            raise
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
        self.server.log_operation(
            "delete",
            self.client_address[0],
            "success",
            username=session["username"],
            path=path_text,
            detail=f"kind={kind}; bytes={item_size}",
        )
        self._send_json(
            200,
            {"ok": True, "path": path_text, "kind": kind, "size": item_size},
        )

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        try:
            status_code = int(code)
        except (TypeError, ValueError):
            status_code = 0
        # Successful browsing, previewing and downloading stays out of server.log.
        # Upload/delete details are written separately by log_operation().
        if status_code >= 400:
            super().log_request(code, size)

    def log_message(self, format_string: str, *args: Any) -> None:
        message = format_string % args
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {message}", flush=True)


def build_config(data_root: Path | None = None, **overrides: Any) -> AppConfig:
    root = (data_root or Path(os.environ.get("ALGO_ROOT", DEFAULT_DATA_ROOT))).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"数据目录不存在或不是文件夹: {root}")
    username = str(overrides.get("username") or os.environ.get("ALGO_USERNAME", DEFAULT_USERNAME))
    password_hash_override = overrides.get("password_hash") or os.environ.get("ALGO_PASSWORD_HASH")
    password_override = overrides.get("password") or os.environ.get("ALGO_PASSWORD")
    if password_hash_override:
        password_hash = str(password_hash_override)
        try:
            parse_password_hash(password_hash)
        except ValueError:
            raise SystemExit("ALGO_PASSWORD_HASH 格式无效")
    elif password_override:
        password_hash = make_password_hash(str(password_override))
    else:
        password_hash = DEFAULT_PASSWORD_HASH
    allowed_hosts_value = str(overrides.get("allowed_hosts") or os.environ.get("ALGO_ALLOWED_HOSTS", ""))
    allowed_hosts = frozenset(
        item.strip().rstrip(".").casefold()
        for item in allowed_hosts_value.split(",")
        if item.strip()
    )
    return AppConfig(
        data_root=root,
        static_root=Path(overrides.get("static_root") or STATIC_DIR),
        username=username,
        password_hash=password_hash,
        max_upload_bytes=int(
            overrides.get("max_upload_bytes")
            or parse_size(os.environ.get("ALGO_MAX_UPLOAD", "40GiB"))
        ),
        min_free_bytes=int(
            overrides.get("min_free_bytes")
            if overrides.get("min_free_bytes") is not None
            else parse_size(os.environ.get("ALGO_MIN_FREE", "2GiB"))
        ),
        preview_bytes=int(
            overrides.get("preview_bytes")
            or parse_size(os.environ.get("ALGO_PREVIEW_SIZE", "1MiB"), default_unit=1)
        ),
        docx_preview_bytes=int(
            overrides.get("docx_preview_bytes")
            or parse_size(os.environ.get("ALGO_DOCX_PREVIEW_SIZE", "10MiB"), default_unit=1)
        ),
        docx_unpacked_bytes=int(
            overrides.get("docx_unpacked_bytes")
            or parse_size(os.environ.get("ALGO_DOCX_UNPACKED_SIZE", "40MiB"), default_unit=1)
        ),
        docx_max_entries=max(
            100,
            int(overrides.get("docx_max_entries") or os.environ.get("ALGO_DOCX_MAX_ENTRIES", "1000")),
        ),
        session_idle_seconds=int(overrides.get("session_idle_seconds") or 8 * 3600),
        session_absolute_seconds=int(overrides.get("session_absolute_seconds") or 24 * 3600),
        cookie_secure=bool(
            overrides.get("cookie_secure")
            if overrides.get("cookie_secure") is not None
            else parse_bool(os.environ.get("ALGO_COOKIE_SECURE"))
        ),
        allow_recursive_delete=bool(
            overrides.get("allow_recursive_delete")
            if overrides.get("allow_recursive_delete") is not None
            else parse_bool(os.environ.get("ALGO_ALLOW_RECURSIVE_DELETE"))
        ),
        allowed_hosts=allowed_hosts,
        operation_log=Path(
            overrides.get("operation_log")
            or overrides.get("audit_log")  # Backward-compatible test/deployment option.
            or os.environ.get("ALGO_OPERATION_LOG")
            or os.environ.get("ALGO_AUDIT_LOG")
            or APP_DIR / "operations.log"
        ),
        max_concurrent_uploads=max(
            1,
            int(overrides.get("max_concurrent_uploads") or os.environ.get("ALGO_MAX_UPLOADS", "2")),
        ),
        request_timeout_seconds=max(
            15,
            int(overrides.get("request_timeout_seconds") or os.environ.get("ALGO_REQUEST_TIMEOUT", "120")),
        ),
    )


def discover_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "服务器局域网IP"
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="算法文件管理网页")
    parser.add_argument("--host", default=os.environ.get("ALGO_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ALGO_PORT", "8080")))
    parser.add_argument("--root", type=Path, default=None, help="要管理的数据根目录")
    args = parser.parse_args()
    os.umask(0o027)
    config = build_config(args.root)
    if config.operation_log == APP_DIR / "operations.log":
        migrate_legacy_operation_log(APP_DIR / "audit.log", config.operation_log)
    server = AlgorithmServer((args.host, args.port), config)
    address, port = server.server_address[:2]
    shown_host = discover_lan_ip() if address in {"0.0.0.0", "::"} else address
    print("算法文件管理服务已启动", flush=True)
    print(f"数据目录: {config.data_root}", flush=True)
    print(f"本机访问: http://127.0.0.1:{port}", flush=True)
    print(f"局域网访问: http://{shown_host}:{port}", flush=True)
    if config.password_hash == DEFAULT_PASSWORD_HASH:
        print("提示: 正在使用文档中配置的默认登录账号。可通过环境变量覆盖。", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止服务…", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
