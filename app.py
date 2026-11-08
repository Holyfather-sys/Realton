"""Realton Telegram Mini App prototype API. Demo points only, never sends funds."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("REALTON_DB", ROOT / "realton.sqlite3"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DEMO_MODE = os.environ.get("REALTON_DEMO_MODE", "true").lower() == "true"
PORT = int(os.environ.get("PORT", "8000"))
CLAIM_COOLDOWN = 10
CLAIM_REWARD = 1
INITDATA_MAX_AGE = 86400


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize():
    with connect_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL REFERENCES users(telegram_id),
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            request_key TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL REFERENCES users(telegram_id),
            points INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ledger_user_time ON ledger(telegram_id, created_at);
        CREATE INDEX IF NOT EXISTS withdrawals_user_time ON withdrawals(telegram_id, created_at);
        """)


def verify_init_data(raw: str):
    if not BOT_TOKEN or not raw:
        return None
    from urllib.parse import parse_qsl
    pairs = parse_qsl(raw, keep_blank_values=True)
    values = dict(pairs)
    supplied_hash = values.pop("hash", "")
    if not supplied_hash:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash):
        return None
    try:
        auth_date = int(values.get("auth_date", "0"))
        user = json.loads(values.get("user", "{}"))
    except (ValueError, json.JSONDecodeError):
        return None
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > INITDATA_MAX_AGE:
        return None
    uid = user.get("id")
    if not uid:
        return None
    first = str(user.get("first_name", "Telegram user"))[:80]
    last = str(user.get("last_name", ""))[:80]
    name = (first + " " + last).strip() or "Telegram user"
    return str(uid), name


def parse_user(handler):
    raw = handler.headers.get("X-Telegram-Init-Data", "")
    account = verify_init_data(raw)
    if account:
        return account
    if DEMO_MODE and not BOT_TOKEN:
        return ("demo-user", "Demo user")
    return None


def ensure_user(db, account):
    uid, name = account
    db.execute("INSERT INTO users(telegram_id,display_name,created_at) VALUES(?,?,?) "
               "ON CONFLICT(telegram_id) DO UPDATE SET display_name=excluded.display_name",
               (uid, name, int(time.time())))


def balance(db, uid):
    row = db.execute("SELECT COALESCE(SUM(amount),0) AS total FROM ledger WHERE telegram_id=?", (uid,)).fetchone()
    return int(row["total"])


class Handler(BaseHTTPRequestHandler):
    server_version = "RealtonPrototype/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def get_account(self):
        account = parse_user(self)
        if not account:
            self.send_json(401, {"error": "Telegram session is invalid or expired. Open this app from Telegram."})
            return None
        return account

    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 4096:
                return None
            obj = json.loads(self.rfile.read(size))
            return obj if isinstance(obj, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "demo_only": DEMO_MODE, "real_token": False})
        if path.startswith("/api/"):
            account = self.get_account()
            if not account:
                return
            uid, name = account
            with connect_db() as db:
                ensure_user(db, account)
                if path == "/api/me":
                    points = balance(db, uid)
                    last = db.execute("SELECT created_at FROM ledger WHERE telegram_id=? AND kind='claim' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
                    remaining = max(0, CLAIM_COOLDOWN - (int(time.time()) - last["created_at"])) if last else 0
                    items = db.execute("SELECT id,points,wallet_address,status,created_at FROM withdrawals WHERE telegram_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
                    return self.send_json(200, {"name": name, "points": points, "claim_reward": CLAIM_REWARD,
                        "cooldown_seconds": CLAIM_COOLDOWN, "cooldown_remaining": remaining,
                        "demo_only": DEMO_MODE, "withdrawals": [dict(x) for x in items]})
            return self.send_json(404, {"error": "Not found"})
        if path in ("/", "/index.html", "/app.js", "/style.css"):
            filename = "index.html" if path == "/" else path.lstrip("/")
            file_path = (ROOT / "web" / filename).resolve()
            if not file_path.is_relative_to((ROOT / "web").resolve()) or not file_path.is_file():
                return self.send_error(404)
            content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(file_path.suffix, "application/octet-stream")
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return self.wfile.write(data)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        account = self.get_account()
        if not account:
            return
        body = self.read_json()
        if body is None:
            return self.send_json(400, {"error": "Invalid request body"})
        uid, _ = account
        with connect_db() as db:
            ensure_user(db, account)
            db.commit()
            if path == "/api/claim":
                key = str(body.get("request_key", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", key):
                    return self.send_json(400, {"error": "A valid request key is required"})
                now = int(time.time())
                try:
                    db.execute("INSERT INTO ledger(telegram_id,amount,kind,created_at,request_key) VALUES(?,?,?,?,?)",
                               (uid, CLAIM_REWARD, "claim", now, key))
                except sqlite3.IntegrityError:
                    current = balance(db, uid)
                    return self.send_json(200, {"ok": True, "points": current, "duplicate": True})
                last = db.execute("SELECT created_at FROM ledger WHERE telegram_id=? AND kind='claim' AND id < last_insert_rowid() ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
                if last and now - last["created_at"] < CLAIM_COOLDOWN:
                    db.rollback()
                    wait = CLAIM_COOLDOWN - (now - last["created_at"])
                    return self.send_json(429, {"error": "Please wait before claiming again.", "cooldown_remaining": wait})
                return self.send_json(200, {"ok": True, "points": balance(db, uid), "duplicate": False})
            if path == "/api/withdraw":
                try:
                    points = int(body.get("points", 0))
                except (TypeError, ValueError):
                    points = 0
                wallet = str(body.get("wallet_address", "")).strip()
                if not 1 <= points <= 100000000:
                    return self.send_json(400, {"error": "Enter a positive whole-number points amount."})
                if not re.fullmatch(r"(?:EQ|UQ)[A-Za-z0-9_-]{46}|-?\d+:[0-9a-fA-F]{64}", wallet):
                    return self.send_json(400, {"error": "Enter a TON address in EQ/UQ or raw workchain:hash format."})
                db.execute("BEGIN IMMEDIATE")
                available = balance(db, uid)
                if available < points:
                    db.rollback()
                    return self.send_json(400, {"error": "Not enough demo points available."})
                db.execute("INSERT INTO ledger(telegram_id,amount,kind,created_at,request_key) VALUES(?,?,?,?,?)",
                           (uid, -points, "withdrawal_reserved", int(time.time()), "reserve_" + secrets.token_urlsafe(24)))
                cur = db.execute("INSERT INTO withdrawals(telegram_id,points,wallet_address,status,created_at) VALUES(?,?,?,'pending',?)",
                                 (uid, points, wallet, int(time.time())))
                return self.send_json(201, {"ok": True, "request_id": cur.lastrowid, "status": "pending",
                    "note": "Request saved for review. This demo does not send TON or tokens."})
        return self.send_json(404, {"error": "Not found"})


def main():
    initialize()
    if not BOT_TOKEN and not DEMO_MODE:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and REALTON_DEMO_MODE=false before using authenticated mode.")
    print(f"Realton prototype listening on http://127.0.0.1:{PORT} (demo_only={DEMO_MODE})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()
