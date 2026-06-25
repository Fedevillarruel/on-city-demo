import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "scheduler.db"


CREATE_CRON_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    run_oncity INTEGER NOT NULL DEFAULT 1,
    run_fravega INTEGER NOT NULL DEFAULT 1,
    run_cetrogar INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


CREATE_RUN_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    message TEXT,
    FOREIGN KEY(job_id) REFERENCES cron_jobs(id)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
        conn.execute(CREATE_CRON_TABLE_SQL)
        conn.execute(CREATE_RUN_LOG_TABLE_SQL)
        conn.commit()


def seed_default_jobs() -> None:
    init_db()
    rows = list_jobs()
    if rows:
        return
    add_job("Diario 09:00", 9, 0, True, True, True, True)
    add_job("Diario 13:00", 13, 0, True, True, True, True)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_jobs() -> list[dict]:
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, name, hour, minute, run_oncity, run_fravega, run_cetrogar,
                   enabled, last_run, created_at, updated_at
            FROM cron_jobs
            ORDER BY hour, minute, id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: int) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            """
            SELECT id, name, hour, minute, run_oncity, run_fravega, run_cetrogar,
                   enabled, last_run, created_at, updated_at
            FROM cron_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def add_job(
    name: str,
    hour: int,
    minute: int,
    run_oncity: bool,
    run_fravega: bool,
    run_cetrogar: bool,
    enabled: bool,
) -> int:
    now = _now_iso()
    with closing(_connect()) as conn:
        cur = conn.execute(
            """
            INSERT INTO cron_jobs (
                name, hour, minute, run_oncity, run_fravega, run_cetrogar,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                int(hour),
                int(minute),
                int(bool(run_oncity)),
                int(bool(run_fravega)),
                int(bool(run_cetrogar)),
                int(bool(enabled)),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_job(
    job_id: int,
    name: str,
    hour: int,
    minute: int,
    run_oncity: bool,
    run_fravega: bool,
    run_cetrogar: bool,
    enabled: bool,
) -> None:
    now = _now_iso()
    with closing(_connect()) as conn:
        conn.execute(
            """
            UPDATE cron_jobs
            SET name = ?,
                hour = ?,
                minute = ?,
                run_oncity = ?,
                run_fravega = ?,
                run_cetrogar = ?,
                enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                int(hour),
                int(minute),
                int(bool(run_oncity)),
                int(bool(run_fravega)),
                int(bool(run_cetrogar)),
                int(bool(enabled)),
                now,
                int(job_id),
            ),
        )
        conn.commit()


def delete_job(job_id: int) -> None:
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM cron_jobs WHERE id = ?", (int(job_id),))
        conn.commit()


def set_job_enabled(job_id: int, enabled: bool) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE cron_jobs SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(bool(enabled)), _now_iso(), int(job_id)),
        )
        conn.commit()


def mark_job_run(job_id: int, run_time_iso: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE cron_jobs SET last_run = ?, updated_at = ? WHERE id = ?",
            (run_time_iso, _now_iso(), int(job_id)),
        )
        conn.commit()


def add_run_log(job_id: int | None, status: str, message: str, started_at: str, finished_at: str | None = None) -> int:
    with closing(_connect()) as conn:
        cur = conn.execute(
            """
            INSERT INTO run_logs (job_id, started_at, finished_at, status, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, started_at, finished_at, status, message),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_run_log(log_id: int, status: str, message: str, finished_at: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """
            UPDATE run_logs
            SET status = ?, message = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, message, finished_at, int(log_id)),
        )
        conn.commit()


def latest_logs(limit: int = 25) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT rl.id, rl.job_id, rl.started_at, rl.finished_at, rl.status, rl.message,
                   cj.name AS job_name
            FROM run_logs rl
            LEFT JOIN cron_jobs cj ON cj.id = rl.job_id
            ORDER BY rl.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]
