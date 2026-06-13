"""Settings service — typed key/value app settings over SQLite.

Distinct from `config.yaml` (declarative infrastructure: hosts/providers/groups).
Settings are the knobs a user flips in the UI at runtime — auth on/off, module
toggles, branding. JSON-valued, cached in-process, with change hooks so other
services can react to a toggle without a restart.

`config.yaml` is data the operator edits on disk; settings are state the app owns.
"""

import json
import re
from collections.abc import Callable

from app.db import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# Known scalar settings: key -> default. The default's Python type is also the
# accepted type on write (bool stays bool, str stays str — see _validate).
DEFAULTS: dict[str, object] = {
    "auth.enabled": True,  # single-user auth on by default (§0); opt-out is loopback-only
    "ui.branding.name": "Dubdeck",
    "bind": "127.0.0.1",  # informational: surfaced in UI, enforced at the bind site
}

# Dynamic key families that don't have a fixed default — module toggles are keyed
# by module name, which we can't enumerate here. The pattern gates the accepted type.
_DYNAMIC: tuple[tuple[re.Pattern[str], type], ...] = (
    (re.compile(r"^modules\.[a-z0-9_-]+\.enabled$"), bool),
)

# Upper bounds on string settings — keeps a pathological value out of the DB and
# the rendered UI. Unlisted string keys are unbounded.
_MAX_LEN: dict[str, int] = {"ui.branding.name": 64}


class UnknownSettingError(KeyError):
    """A key that is neither a known scalar nor a recognised dynamic family."""


class InvalidSettingError(ValueError):
    """A value whose type doesn't match the setting's declared type."""


def _expected_type(key: str) -> type | None:
    if key in DEFAULTS:
        return type(DEFAULTS[key])
    for pattern, typ in _DYNAMIC:
        if pattern.match(key):
            return typ
    return None


class SettingsService:
    """In-process cache over the `settings` table. Read paths are sync (cache only);
    writes hit SQLite then fire change hooks."""

    def __init__(self, db: Database):
        self._db = db
        self._cache: dict[str, object] = {}
        self._hooks: list[Callable[[str, object], None]] = []

    async def init(self) -> None:
        await self._db.init()
        await self._db.execute(_SCHEMA)
        for row in await self._db.fetchall("SELECT key, value FROM settings"):
            self._cache[row["key"]] = json.loads(row["value"])

    def get(self, key: str, default: object = None) -> object:
        if key in self._cache:
            return self._cache[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def all(self) -> dict[str, object]:
        """Known defaults overlaid with everything persisted (incl. dynamic keys)."""
        return {**DEFAULTS, **self._cache}

    async def set(self, key: str, value: object) -> None:
        self._validate(key, value)
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self._cache[key] = value
        for hook in self._hooks:
            hook(key, value)

    def subscribe(self, hook: Callable[[str, object], None]) -> None:
        """Register a callback fired as hook(key, value) after every set()."""
        self._hooks.append(hook)

    @staticmethod
    def _validate(key: str, value: object) -> None:
        expected = _expected_type(key)
        if expected is None:
            raise UnknownSettingError(key)
        # bool is an int subclass — pin each type exactly so True can't pass as an int
        # setting and 1 can't pass as a bool one.
        if expected is bool:
            ok = isinstance(value, bool)
        elif expected is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, expected)
        if not ok:
            raise InvalidSettingError(f"setting {key!r} expects {expected.__name__}")
        limit = _MAX_LEN.get(key)
        if limit is not None and isinstance(value, str) and len(value) > limit:
            raise InvalidSettingError(f"setting {key!r} exceeds {limit} characters")
