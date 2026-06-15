# Rancher Provider for Dubdeck — Specification
**Author:** N1H Tech (Gemma)
**Date:** 2026-06-15
**Status:** Draft
**Rancher version tested:** v2.8.2 (rancher/rancher:latest)

---

## Overview

The Rancher provider manages Kubernetes workloads through Rancher's REST API and the
cluster-level k8s proxy. It treats each **cluster** as a resource group, and
**workloads** (Deployments, StatefulSets, DaemonSets) as individual resources.

Rancher version: v2.8.2 running as a Docker container (`rancher/rancher:latest`)
on `__IP__` (VM with Docker, 4 NICs across 4 LAN subnets).

---

## Architecture

```
Dubdeck backend
    │
    │  HTTPS + Bearer token auth
    │
    ├──► Rancher API (port 443): /v3/*, /v3-public/*
    │      → clusters, projects, workloads, pods
    │
    └──► Cluster k8s proxy (port 443): /k8s/clusters/<cluster>/*
           → pod logs, exec, direct k8s API access
```

**Auth:** Login token obtained from `POST /v3-public/localProviders/local?action=login`.
Token format: `token-<id>:<secret>` (Bearer auth).

**Token lifetime:** 57600000ms (≈ 16.7 hours). The provider should refresh on 401.

---

## API Surface

### Auth
```
POST /v3-public/localProviders/local?action=login
Body: {"username": "<user>", "password": "<pass>"}
200: {"token": {"value": "token-<id>:<secret>"}}
```

### Clusters
```
GET /v3/clusters
Authorization: Bearer <token>
200: {"data": [{"id": "c-m-xxx", "name": "daggerfall", "state": "active|unavailable"},
              {"id": "local", "name": "local", "state": "active"}]}
```

### Projects (per cluster)
```
GET /v3/projects?clusterId=<cluster-id>
200: {"data": [{"id": "local:p-8kcg8", "name": "System"},
               {"id": "local:p-n897b", "name": "Default"}]}
```

### Workloads (per project)
```
GET /v3/project/<projectId>/deployments       # Deployment workload type
GET /v3/project/<projectId>/statefulsets       # StatefulSet
GET /v3/project/<projectId>/daemonsets         # DaemonSet
GET /v3/project/<projectId>/jobs               # Job
GET /v3/project/<projectId>/cronjobs           # CronJob

Response: {"data": [{"type": "deployment",
                     "id": "deployment:cattle-system:rancher-webhook",
                     "name": "rancher-webhook",
                     "state": "active|paused|updating",
                     "replicas": 1,
                     "actions": {"pause": ..., "resume": ..., "redeploy": ...}}]}
```

### Workload actions
```
POST /v3/project/<projectId>/<workloadType>/<workloadId>?action=pause
POST /v3/project/<projectId>/<workloadType>/<workloadId>?action=resume
POST /v3/project/<projectId>/<workloadType>/<workloadId>?action=redeploy
```
Returns: `{}` on success (empty body, no error = OK).

**Stop (scaling to 0):** No direct stop action. Uses k8s proxy:
```
PATCH /k8s/clusters/<cluster>/apis/apps/v1/namespaces/<ns>/deployments/<name>
Body: [{"op": "replace", "path": "/spec/replicas", "value": 0}]
```

**Start (scale to 1):**
```
PATCH /k8s/clusters/<cluster>/apis/apps/v1/namespaces/<ns>/deployments/<name>
Body: [{"op": "replace", "path": "/spec/replicas", "value": 1}]
```

### Pods
```
GET /v3/project/<projectId>/pods
200: {"data": [{"id": "cattle-system:rancher-webhook-xxx",
                "state": "running|terminated|unavailable",
                "restartCount": 0}]}
```

### Pod logs (via k8s proxy)
```
GET /k8s/clusters/<cluster>/api/v1/namespaces/<ns>/pods/<pod-name>/log
Authorization: Bearer <token>
200: <plain text log output>
```

### Cluster kubeconfig (for exec/port-forward)
```
POST /v3/clusters/<cluster-id>?action=generateKubeconfig
Authorization: Bearer <token>
200: {"config": "<kubeconfig-yaml>"}
```

---

## Resource Identification

```
ref format: <provider-id>/<cluster-id>/<project-id>/<workload-type>/<namespace>/<name>
example:  rancher/local/local:p-8kcg8/deployment/cattle-system/rancher-webhook
```

The provider's resource id is: `<cluster-id>/<project-id>/<type>/<namespace>/<name>`

**Cluster `local`** is the Rancher server's local k3s cluster.
**Cluster `daggerfall`** (c-m-wxln69zp) is unavailable — node is offline due to
hardware failure (memory card). Provider should report it as `unavailable`.

---

## Capability Map

| Capability | Implementation | Notes |
|---|---|---|
| `START` | Scale replicas → 1 via k8s proxy PATCH | If already running, no-op |
| `STOP` | Scale replicas → 0 via k8s proxy PATCH | Deployment only |
| `FORCE_STOP` | Same as STOP (k8s has no force) | |
| `RESTART` | `?action=redeploy` on the workload | |
| `PAUSE` | `?action=pause` | |
| `RESUME` | `?action=resume` | |
| `LOGS` | k8s proxy: `/namespaces/<ns>/pods/<pod>/log` | |
| `SNAPSHOT_LIST` | ❌ Not applicable | |
| `SNAPSHOT_CREATE` | ❌ Not applicable | |
| `DISK_STATS` | ❌ Not applicable | |
| `CONSOLE` | ❌ Not applicable (use kubectl exec) | |

---

## Implementation

**File:** `backend/app/providers/rancher.py`

### Config fields
```yaml
providers:
  - id: rancher-main
    type: rancher
    url: https://__IP__
    username: admin          # optional if token_secret_env provided
    token_secret_env: RANCHER_TOKEN   # env var with "token-<id>:<secret>"
    # password: StormSurge81  # alternatively inline password
    verify_tls: false        # always false for self-signed certs
```

### Auth flow
1. If `username` + `password` provided: `POST /v3-public/localProviders/local?action=login`
2. If `token` (inline or env) provided: use directly as Bearer token
3. On 401: attempt re-login if credentials available, else raise `Unauthorized`

### Resource model
- `kind = ResourceKind.VM` for the cluster itself (conceptually)
- `kind = ResourceKind.CONTAINER` for workloads (Deployment/StatefulSet/DaemonSet)
- Each **project** in a cluster is a separate resource list
- A group in Dubdeck maps to `provider/cluster` → surfaces all projects' workloads

### Groups
```yaml
groups:
  rancher-local:
    label: "Rancher / Local Cluster"
    auto: rancher-main/local          # cluster id = local
```

---

## Config Example

```yaml
providers:
  - id: rancher-main
    type: rancher
    url: https://__IP__
    username: admin
    token_secret_env: RANCHER_TOKEN
    verify_tls: false

groups:
  rancher-local:
    label: "Rancher / Local"
    auto: rancher-main/local
```

**Set token:**
```bash
export RANCHER_TOKEN="token-fjltr:__TOKEN__"
```

Or get a fresh one:
```bash
curl -sk -X POST https://__IP__/v3-public/localProviders/local?action=login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"StormSurge81"}'
```

---

## N1H Tech Deployment

| Endpoint | Value |
|---|---|
| Rancher URL | `https://__IP__` |
| Admin user | `admin` |
| Password | `StormSurge81` |
| Local cluster | `local` (k3s, active, healthy) |
| Downstream cluster | `daggerfall` (`c-m-wxln69zp`, unavailable — Daggerfall ARM node offline) |
| Current token | `token-fjltr:__TOKEN__` (expires ~16h) |

**Token creation via API:**
```bash
curl -sk -X POST https://__IP__/v3/tokens \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"type":"token","name":"dubdeck"}'
```

---

## Notes

- **Daggerfall cluster:** Orphaned — node hardware failure (memory card). Will show
  `unavailable`. Can be removed from Rancher when Daggerfall is repaired/replaced.
- **Local cluster:** The k3s inside the Rancher container. Some system pods
  (`coredns`, `fleet-controller`) showed `unavailable` briefly after Rancher container
  restart but recover on their own.
- **Self-signed cert:** Rancher uses a dynamic self-signed cert (`O=dynamic`).
  `verify_tls: false` is always needed in homelab setups.
- **Pause vs stop:** k8s doesn't have a native stop. Pause (Rancher extension) puts
  the deployment in a `paused` state, no new pods are started. Resume undoes this.
- **Logs:** Pod must be `running` state for logs to be accessible. Unavailable/terminated
  pods return a k8s Status error.
