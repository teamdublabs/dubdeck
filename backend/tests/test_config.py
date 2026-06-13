from pathlib import Path

import pytest
import yaml

from app.config import Config, load_config

CONFIG_FIXTURE = Path(__file__).parent / "fixtures" / "config.yaml"


def base_data() -> dict:
    return yaml.safe_load(CONFIG_FIXTURE.read_text())


def test_config_loads():
    cfg = load_config(CONFIG_FIXTURE)
    assert set(cfg.groups) == {"lab-03", "lab-01", "lab-02"}
    assert cfg.hosts["server01"].transport == "ssh"
    assert cfg.hosts["workstation"].stats == "macos"
    assert cfg.hosts["relay-fw"].stats is None
    assert {p.id for p in cfg.providers} == {"host02-parallels", "host01-kvm"}
    assert cfg.provider_host("host01-kvm") == "server01"
    assert cfg.groups["lab-01"].policies.snapshot_before_stop is True
    assert cfg.groups["lab-01"].policies.start_first == ["host02-parallels/gateway-01"]


def test_empty_config_is_valid():
    cfg = Config()
    assert cfg.is_empty
    assert cfg.hosts == {} and cfg.providers == [] and cfg.groups == {}


def test_missing_config_file_boots_empty(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg.is_empty


def test_blank_config_file_boots_empty(tmp_path):
    blank = tmp_path / "config.yaml"
    blank.write_text("\n  \n")
    assert load_config(blank).is_empty


def test_malformed_config_file_still_raises(tmp_path):
    # A file that EXISTS but references a non-existent provider must not be
    # silently masked as "empty" — only a missing file means fresh-install.
    bad = tmp_path / "config.yaml"
    bad.write_text(yaml.safe_dump({"groups": {"g": {"label": "G", "members": ["ghost/x"]}}}))
    with pytest.raises(ValueError, match="unknown provider"):
        load_config(bad)


def test_group_for_ref():
    cfg = load_config(CONFIG_FIXTURE)
    name, _ = cfg.group_for_ref("host02-parallels/pentest-vm")
    assert name == "lab-01"
    name, _ = cfg.group_for_ref("host01-kvm/gateway-02")
    assert name == "lab-02"
    assert cfg.group_for_ref("host01-kvm/NixOS") is None


def test_unknown_provider_host_rejected():
    data = base_data()
    data["providers"][0]["host"] = "ghost"
    with pytest.raises(ValueError, match="unknown host"):
        Config.model_validate(data)


def test_unknown_provider_type_rejected():
    data = base_data()
    data["providers"][0]["type"] = "vmware"
    with pytest.raises(ValueError, match="unknown provider type"):
        Config.model_validate(data)


def test_duplicate_provider_id_rejected():
    data = base_data()
    data["providers"].append({"id": "host01-kvm", "type": "libvirt", "host": "server01"})
    with pytest.raises(ValueError, match="duplicate provider id"):
        Config.model_validate(data)


def test_member_references_unknown_provider_rejected():
    data = base_data()
    data["groups"]["lab-02"]["members"].append("ghost-provider/x")
    with pytest.raises(ValueError, match="unknown provider"):
        Config.model_validate(data)


def test_start_first_must_be_a_member():
    data = base_data()
    data["groups"]["lab-02"]["policies"]["start_first"] = ["host01-kvm/not-a-member"]
    with pytest.raises(ValueError, match="start_first"):
        Config.model_validate(data)


def test_ready_probe_must_be_a_member():
    data = base_data()
    data["groups"]["lab-02"]["policies"]["ready_probe"] = {"ref": "host01-kvm/ghost"}
    with pytest.raises(ValueError, match="ready_probe"):
        Config.model_validate(data)


def test_auto_and_members_mutually_exclusive():
    data = base_data()
    data["groups"]["lab-02"]["auto"] = "host01-kvm"
    with pytest.raises(ValueError, match="members.*or.*auto|auto.*not both"):
        Config.model_validate(data)


def test_missing_ssh_host_user_rejected():
    data = base_data()
    del data["hosts"]["server01"]["user"]
    with pytest.raises(ValueError, match="user"):
        Config.model_validate(data)


def test_local_transport_needs_no_address():
    data = base_data()
    data["hosts"]["this-box"] = {"transport": "local", "stats": "macos"}
    cfg = Config.model_validate(data)
    assert cfg.hosts["this-box"].address is None


# --- API provider (proxmox) validation: URL + token, no host ---


def _proxmox_provider(**overrides) -> dict:
    base = {
        "id": "pve",
        "type": "proxmox",
        "url": "https://pve.example:8006",
        "token_id": "dubdeck@pam!dubdeck",
        "token_secret_env": "DUBDECK_PVE_TOKEN",
    }
    base.update(overrides)
    return base


def test_proxmox_provider_validates_without_host():
    data = base_data()
    data["providers"].append(_proxmox_provider())
    cfg = Config.model_validate(data)
    pve = next(p for p in cfg.providers if p.id == "pve")
    assert pve.host is None and pve.verify_tls is True


def test_proxmox_requires_url():
    data = base_data()
    data["providers"].append(_proxmox_provider(url=None))
    with pytest.raises(ValueError, match="require a 'url'"):
        Config.model_validate(data)


def test_proxmox_requires_token_fields():
    data = base_data()
    data["providers"].append(_proxmox_provider(token_secret_env=None))
    with pytest.raises(ValueError, match="token_id.*token_secret_env"):
        Config.model_validate(data)


def test_command_provider_still_requires_host():
    data = base_data()
    data["providers"].append({"id": "extra-kvm", "type": "libvirt"})
    with pytest.raises(ValueError, match="require a 'host'"):
        Config.model_validate(data)
