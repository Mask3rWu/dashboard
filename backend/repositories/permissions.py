"""Repository helpers for app context and capability lookups."""

from __future__ import annotations

from datetime import datetime


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, now_text()),
    )


def get_user_by_session_hash(conn, token_hash: str) -> dict | None:
    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.created_at, u.password_changed_at
           FROM auth_sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash=?
             AND (s.expires_at IS NULL OR s.expires_at > datetime('now'))""",
        (token_hash,),
    ).fetchone()
    return dict(row) if row else None
