"""Settings service — typed key/value over SQLite (Phase 3.1)."""

import pytest

from app.settings import (
    DEFAULTS,
    InvalidSettingError,
    SettingsService,
    UnknownSettingError,
)


@pytest.fixture
async def settings(db):
    svc = SettingsService(db)
    await svc.init()
    return svc


async def test_get_returns_default_when_unset(settings):
    assert settings.get("auth.enabled") is True
    assert settings.get("ui.branding.name") == "Dubdeck"


async def test_get_unknown_key_returns_supplied_default(settings):
    assert settings.get("nope.not.here", "fallback") == "fallback"
    assert settings.get("nope.not.here") is None


async def test_set_then_get_roundtrips(settings):
    await settings.set("auth.enabled", False)
    assert settings.get("auth.enabled") is False


async def test_set_persists_across_reload(db):
    svc = SettingsService(db)
    await svc.init()
    await svc.set("ui.branding.name", "Homelab")
    # A fresh service over the same DB sees the persisted value (cache repopulates).
    reloaded = SettingsService(db)
    await reloaded.init()
    assert reloaded.get("ui.branding.name") == "Homelab"


async def test_all_overlays_defaults_with_stored(settings):
    await settings.set("ui.branding.name", "Homelab")
    snapshot = settings.all()
    assert snapshot["ui.branding.name"] == "Homelab"
    assert snapshot["auth.enabled"] is True  # untouched default still present
    assert set(DEFAULTS) <= set(snapshot)


async def test_dynamic_module_toggle_key_accepted(settings):
    await settings.set("modules.egress.enabled", True)
    assert settings.get("modules.egress.enabled") is True
    assert settings.all()["modules.egress.enabled"] is True


async def test_unknown_key_rejected(settings):
    with pytest.raises(UnknownSettingError):
        await settings.set("totally.made.up", 1)
    # A module key that doesn't match the toggle family is also unknown.
    with pytest.raises(UnknownSettingError):
        await settings.set("modules.egress.color", "red")


async def test_type_mismatch_rejected(settings):
    with pytest.raises(InvalidSettingError):
        await settings.set("auth.enabled", "yes")  # bool expected
    with pytest.raises(InvalidSettingError):
        await settings.set("ui.branding.name", 42)  # str expected


async def test_branding_name_length_capped(settings):
    await settings.set("ui.branding.name", "x" * 64)  # at the limit is fine
    with pytest.raises(InvalidSettingError):
        await settings.set("ui.branding.name", "x" * 65)


async def test_bool_setting_rejects_int_lookalike(settings):
    # bool is an int subclass; 1 must NOT pass as a bool setting.
    with pytest.raises(InvalidSettingError):
        await settings.set("auth.enabled", 1)


async def test_change_hook_fires_on_set(settings):
    seen: list[tuple[str, object]] = []
    settings.subscribe(lambda key, value: seen.append((key, value)))
    await settings.set("auth.enabled", False)
    assert seen == [("auth.enabled", False)]


async def test_failed_set_does_not_fire_hook(settings):
    seen: list[tuple[str, object]] = []
    settings.subscribe(lambda key, value: seen.append((key, value)))
    with pytest.raises(UnknownSettingError):
        await settings.set("bogus", 1)
    assert seen == []
