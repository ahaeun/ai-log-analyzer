import sqlite3
from datetime import datetime, timezone


def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS servers (
                server_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                username TEXT NOT NULL,
                ssh_key_path TEXT NOT NULL,
                log_path TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('default', 'custom')),
                custom_pattern TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_emails (
                email TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                log_level TEXT NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL,
                stack_trace TEXT NOT NULL,
                raw_log TEXT NOT NULL,
                ai_analysis TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                notified_at TEXT,
                received_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def list_servers(db_path):
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM servers ORDER BY server_id").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_server(db_path, server_id):
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE server_id = ?", (server_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    now = _now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO servers
               (server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_server(db_path, server_id, host, port, username, ssh_key_path, log_path, format, custom_pattern):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE servers SET host=?, port=?, username=?, ssh_key_path=?, log_path=?,
               format=?, custom_pattern=?, updated_at=? WHERE server_id=?""",
            (host, port, username, ssh_key_path, log_path, format, custom_pattern, _now_iso(), server_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_server(db_path, server_id):
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM servers WHERE server_id = ?", (server_id,))
        conn.commit()
    finally:
        conn.close()


def list_allowed_emails(db_path):
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM allowed_emails ORDER BY email").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def add_allowed_email(db_path, email):
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO allowed_emails (email, created_at) VALUES (?, ?)",
            (email, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_allowed_email(db_path, email):
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM allowed_emails WHERE email = ?", (email,))
        conn.commit()
    finally:
        conn.close()


def insert_error(db_path, server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
                  ai_analysis=None, notified=False, notified_at=None):
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO errors
               (server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
                ai_analysis, notified, notified_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id, timestamp, log_level, error_type, message, stack_trace, raw_log,
             ai_analysis, 1 if notified else 0, notified_at, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def query_errors(db_path, server_id=None, date_from=None, date_to=None, error_type=None, limit=50, offset=0):
    conditions = []
    params = []
    if server_id:
        conditions.append("server_id = ?")
        params.append(server_id)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to)
    if error_type:
        conditions.append("error_type LIKE ?")
        params.append(f"%{error_type}%")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM errors {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_servers(db_path):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]
    finally:
        conn.close()


def count_errors_since(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE timestamp >= ?", (since_iso,)
        ).fetchone()["c"]
    finally:
        conn.close()


def count_notified_since(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM errors WHERE notified = 1 AND notified_at >= ?", (since_iso,)
        ).fetchone()["c"]
    finally:
        conn.close()


def error_counts_by_day(db_path, since_iso):
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS c
               FROM errors WHERE timestamp >= ? GROUP BY day ORDER BY day""",
            (since_iso,),
        ).fetchall()
        return [{"day": row["day"], "count": row["c"]} for row in rows]
    finally:
        conn.close()


def recent_errors(db_path, limit=5):
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM errors ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
