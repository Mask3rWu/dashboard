"""Local account and session helpers for research-mode authentication."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
SESSION_DAYS = 7


def _utcnow_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256 with a per-password salt."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against this app's stored hash format."""
    try:
        scheme, iterations_text, salt, expected = password_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def ensure_builtin_admin(conn) -> None:
    """Create the default research admin account if it does not exist."""
    row = conn.execute("SELECT id FROM users WHERE username=?", ("admin",)).fetchone()
    if row:
        return
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        ("admin", hash_password("123456"), "admin"),
    )


def create_session(conn, user_id: int) -> str:
    """Create a bearer token session and return the raw token once."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (token_hash, user_id, expires_at),
    )
    return token


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_hash_token(token),))


def change_password(conn, user_id: int, old_password: str, new_password: str) -> None:
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not row or not verify_password(old_password, row["password_hash"]):
        raise ValueError("旧密码不正确")
    conn.execute(
        "UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
        (hash_password(new_password), _utcnow_text(), user_id),
    )
    conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))


def create_user(conn, username: str, password: str, role: str = "user") -> int:
    if role not in ("admin", "user"):
        raise ValueError("角色必须是 admin 或 user")
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hash_password(password), role),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def extract_bearer_token(request) -> str | None:
    auth = request.headers.get("authorization") if request else None
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    token = request.headers.get("x-session-token") if request else None
    return token.strip() if token else None


def session_token_hash(token: str) -> str:
    return _hash_token(token)
