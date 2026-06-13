from app.main import build_providers
from app.services.stats import ResourceStatsService, parse_stats
from tests.conftest import FIXTURES


def test_parse_linux_stats():
    stats = parse_stats("linux", (FIXTURES / "linux_stats.txt").read_text())
    assert stats.mem_total == 65170083840
    assert stats.disk_total is not None


def test_parse_macos_stats():
    stats = parse_stats("macos", (FIXTURES / "mac_stats.txt").read_text())
    assert stats.mem_total > 0
    assert stats.mem_used > 0


async def test_resource_disk_stats(db, config, transports):
    transports["workstation"].respond(
        "dubdeck-vm-disks", stdout=(FIXTURES / "dubdeck_vm_disks.txt").read_text()
    )
    transports["server01"].respond(
        "virsh domstats --block --state",
        stdout=(FIXTURES / "virsh_domstats_block.txt").read_text(),
    )
    svc = ResourceStatsService(config, build_providers(config, transports))
    body = await svc.snapshot()
    assert body["resources"]["host02-parallels/pentest-vm"]["disk_bytes"] == 93914044 * 1024
    # the du sweep also sees non-lab VMs — they must be dropped
    assert "host02-parallels/NixOS" not in body["resources"]
