"""Seeded FakeHttpClient for the Proxmox provider — the API-side analogue of the
prlctl_/virsh_ text fixtures. A fictional one-node cluster (`pve`) with one qemu
guest (vmid 100, running) and one lxc guest (vmid 101, stopped). Reused by the
contract suite and the proxmox unit tests so there's one canonical happy path.
"""

from urllib.parse import quote

from app.httpclient import FakeHttpClient
from app.providers.proxmox import API, ProxmoxProvider

# A representative UPID — every mutating action in the happy path returns this
# and the task-status poll below reports it finished OK.
UPID = "UPID:pve:0000ABCD:00ABCDEF:6500FEED:qmstart:100:root@pam:"
TASK_PATH = f"{API}/nodes/pve/tasks/{quote(UPID, safe='')}/status"


def healthy_client() -> FakeHttpClient:
    c = FakeHttpClient(label="proxmox")
    c.respond("GET", f"{API}/nodes", body={"data": [{"node": "pve", "status": "online"}]})
    c.respond(
        "GET",
        f"{API}/nodes/pve/qemu",
        body={
            "data": [{"vmid": 100, "name": "webvm", "status": "running", "maxdisk": 34359738368}]
        },
    )
    c.respond(
        "GET",
        f"{API}/nodes/pve/lxc",
        body={"data": [{"vmid": 101, "name": "dns", "status": "stopped", "maxdisk": 8589934592}]},
    )
    c.respond(
        "GET",
        f"{API}/nodes/pve/status",
        body={
            "data": {
                "loadavg": ["0.15", "0.22", "0.30"],
                "memory": {"total": 67430322176, "used": 18253611008},
                "rootfs": {"total": 100861726720, "used": 27917287424},
            }
        },
    )
    # Mutations on both guests resolve through the same UPID happy path.
    for vtype, vmid in (("qemu", 100), ("lxc", 101)):
        base = f"{API}/nodes/pve/{vtype}/{vmid}"
        for action in ("start", "shutdown", "stop", "suspend"):
            c.respond("POST", f"{base}/status/{action}", body={"data": UPID})
        c.respond("POST", f"{base}/snapshot", body={"data": UPID})
    c.respond(
        "GET",
        f"{API}/nodes/pve/qemu/100/snapshot",
        body={
            "data": [
                {"name": "current"},  # synthetic live-state entry — provider drops it
                {"name": "pre-update", "snaptime": 1700000000},
            ]
        },
    )
    c.respond("GET", f"{API}/nodes/pve/lxc/101/snapshot", body={"data": []})
    c.respond("GET", TASK_PATH, body={"data": {"status": "stopped", "exitstatus": "OK"}})
    return c


def healthy_provider(poll_interval: float = 0.0) -> ProxmoxProvider:
    # poll_interval=0 keeps UPID-polling tests instant.
    return ProxmoxProvider("pve", healthy_client(), poll_interval=poll_interval)
