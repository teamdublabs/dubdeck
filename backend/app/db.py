"""One shared aiosqlite connection per process — WAL mode, opened once at startup.

Replaces connect-per-call: the ops log and egress engine write on every action
and the status loop reads every few seconds, so connection churn was pure waste.
"""

import aiosqlite


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # WAL lets the odd external reader (debugging with sqlite3) coexist
        # with the long-lived writer connection.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.init() not called")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
