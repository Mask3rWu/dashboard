"""Repository helpers for local users and bearer sessions."""

from __future__ import annotations


def get_user_by_username(conn, username: str):
    return conn.execute(
        """SELECT id, username, password_hash, role, created_at, password_changed_at
           FROM users WHERE username=?""",
        (username,),
    ).fetchone()


def user_exists(conn, username: str) -> bool:
    return get_user_by_username(conn, username) is not None


def get_password_hash(conn, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT password_hash FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    return row["password_hash"] if row else None


def insert_user(conn, username: str, password_hash: str, role: str) -> int:
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, password_hash, role),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_password(conn, user_id: int, password_hash: str, changed_at: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
        (password_hash, changed_at, user_id),
    )


def insert_session(conn, token_hash: str, user_id: int, expires_at: str) -> None:
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (token_hash, user_id, expires_at),
    )


def delete_session_by_hash(conn, token_hash: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))


def delete_sessions_for_user(conn, user_id: int) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
