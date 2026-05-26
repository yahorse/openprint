from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openprint.models import DuplexMode, Job, JobStatus

DEFAULT_DB_PATH = Path.home() / ".openprint" / "jobs.db"


class JobStore:
    """SQLite-backed persistent job history."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                printer TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                pages_total INTEGER NOT NULL DEFAULT 0,
                pages_printed INTEGER NOT NULL DEFAULT 0,
                copies INTEGER NOT NULL DEFAULT 1,
                color INTEGER NOT NULL DEFAULT 1,
                duplex TEXT NOT NULL DEFAULT 'none',
                media TEXT NOT NULL DEFAULT 'a4',
                priority INTEGER NOT NULL DEFAULT 50,
                error TEXT,
                file_size INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_printer ON jobs(printer)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)
        """)
        self._conn.commit()

    def save(self, job: Job, printer: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO jobs
               (id, printer, status, created_at, pages_total, pages_printed,
                copies, color, duplex, media, priority, error, file_size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                printer,
                job.status.value,
                job.created_at.isoformat(),
                job.pages_total,
                job.pages_printed,
                job.copies,
                int(job.color),
                job.duplex.value,
                job.media,
                job.priority,
                job.error,
                job.file_size,
            ),
        )
        self._conn.commit()

    def update_status(
        self, job_id: str, status: str, pages_printed: int = 0, error: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ?, pages_printed = ?, error = ? WHERE id = ?",
            (status, pages_printed, error, job_id),
        )
        self._conn.commit()

    def get(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def list_jobs(
        self,
        printer: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        conditions: list[str] = []
        params: list[Any] = []

        if printer:
            conditions.append("printer = ?")
            params.append(printer)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM jobs {where}", params
        ).fetchone()
        total = count_row[0]

        rows = self._conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return [self._row_to_job(r) for r in rows], total

    def clear_old(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc).isoformat()
        result = self._conn.execute(
            "DELETE FROM jobs WHERE created_at < datetime(?, '-' || ? || ' days')",
            (cutoff, days),
        )
        self._conn.commit()
        return result.rowcount

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            status=JobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            pages_total=row["pages_total"],
            pages_printed=row["pages_printed"],
            copies=row["copies"],
            color=bool(row["color"]),
            duplex=DuplexMode(row["duplex"]),
            media=row["media"],
            priority=row["priority"],
            error=row["error"],
            file_size=row["file_size"],
        )
