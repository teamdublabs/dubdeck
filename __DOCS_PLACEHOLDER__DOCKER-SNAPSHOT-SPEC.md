# Docker/Podman Snapshot Support — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft

---

## Overview

Add snapshot capabilities to the Docker/Podman provider. Container snapshots are implemented as **Docker images** created via `docker commit`. The image serves as a recoverable restore point — `docker run` from the saved image starts a container with the saved filesystem state.

**Naming convention:** `dubdeck-snap-<container-name>-<unix-timestamp>`

---

## Implementation

### Snapshot Create
```bash
docker commit <container> dubdeck-snap-<container>-<unix-timestamp>
```
- Creates a new image tagged with the snapshot name
- Image contains the container's entire writable filesystem at commit time
- Tag format: `dubdeck-snap-<container>-<timestamp>` (e.g. `dubdeck-snap-myapp-1750000000`)

### Snapshot List
```bash
docker images --format '{{json .}}' | grep dubdeck-snap
```
- Filter images whose Repository starts with `dubdeck-snap-`
- Parse image metadata: tag, created time, size
- Map back to container name from the tag

### Snapshot Delete
```bash
docker rmi dubdeck-snap-<container>-<timestamp>
```
- Removes the saved image
- Does NOT affect the original container

### Snapshot Restore (not implemented — deliberate boundary)
- To restore: `docker run -d dubdeck-snap-<container>-<timestamp> <original-cmd>`
- This creates a **new** container from the image — it does not overwrite the original
- This is the same boundary as VM snapshot restore: manual, conscious act

---

## Docker/Podman Provider Changes

**New capabilities to add to `DockerProvider.capabilities`:**
- `SNAPSHOT_LIST`
- `SNAPSHOT_CREATE`

**Note:** `SNAPSHOT_DELETE` is also a natural fit — removing old snapshots keeps the image list clean.

**Methods to add:**
```python
async def snapshot_list(self, rid: str) -> list[Snapshot]:
    # List images matching dubdeck-snap-<rid>-*
    # Return Snapshot(name=image_tag, created=created_at, current=False)

async def snapshot_create(self, rid: str, name: str, timeout: float = 300.0) -> None:
    # docker commit <container> dubdeck-snap-<rid>-<timestamp>
```

**Parse image list output:**
```bash
docker images --format '{{json .}}' --filter 'reference=dubdeck-snap*'
```
Returns NDJSON, one line per image. Fields: `Repository`, `Tag`, `CreatedAt`, `Size`.

---

## Podman Support

Podman has the same `commit`/`images`/`rmi` commands with identical syntax. Since `PodmanProvider` extends `DockerProvider` and only changes the binary name, snapshot commands work identically — no code changes needed beyond adding the capability.

---

## Key Design Decision

**Why images, not docker checkpoint?**

`docker checkpoint` requires the application inside the container to support CRIU (Checkpoint/Restore in Userspace). Most containers don't have this. `docker commit` works universally — any container, any application.

**Trade-off:** A `docker commit` snapshot saves the filesystem but not the running process state. For a web server, this means the files are restored but active connections are lost. This is acceptable for the Dubdeck use case — it's a "roll back to a known good filesystem state" tool, not a live migration system.

---

## Config Changes

None — `binary_name` already handles podman/docker distinction. Snapshot capabilities are added by updating the provider's `capabilities` frozenset in code.

---

## Testing

1. Start a container: `docker run -d --name test-web nginx`
2. Create a snapshot: `docker commit test-web dubdeck-snap-test-web-$(date +%s)`
3. Modify the container: `docker exec test-web sh -c 'echo broken > /tmp/test.txt'`
4. List snapshots: check image appears with correct tag
5. Restore: `docker run -d --name test-web-restored dubdeck-snap-test-web-<timestamp> nginx`
6. Verify: `docker exec test-web-restored cat /tmp/test.txt` — should NOT show "broken"
7. Delete snapshot: `docker rmi dubdeck-snap-test-web-<timestamp>`
