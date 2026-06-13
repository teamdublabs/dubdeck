"""Ops log — every mutating action Dubdeck takes, persisted to SQLite."""

import time
from dataclasses import dataclass

from app.db import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ops_action ON ops(action)",
    "CREATE INDEX IF NOT EXISTS idx_ops_target ON ops(target)",
)


@dataclass
class OpsLog:
    db: Database

    async def init(self) -> None:
        await self.db.init()
        await self.db.execute(_SCHEMA)
        for index in _INDEXES:
            await self.db.execute(index)

    async def record(self, action: str, target: str, ok: bool, detail: str = "") -> None:
        await self.db.execute(
            "INSERT INTO ops (ts, action, target, ok, detail) VALUES (?, ?, ?, ?, ?)",
            (time.time(), action, target, int(ok), detail),
        )

    async def recent(
        self,
        limit: int = 100,
        before_id: int | None = None,
        action: str | None = None,
        target: str | None = None,
        failures_only: bool = False,
    ) -> list[dict]:
        where, params = ["1=1"], []
        if before_id is not None:
            where.append("id < ?")
            params.append(before_id)
        if action:
            where.append("action LIKE ?")
            params.append(f"{action}%")
        if target:
            where.append("target = ?")
            params.append(target)
        if failures_only:
            where.append("ok = 0")
        rows = await self.db.fetchall(
            "SELECT id, ts, action, target, ok, detail FROM ops "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(row) | {"ok": bool(row["ok"])} for row in rows]
