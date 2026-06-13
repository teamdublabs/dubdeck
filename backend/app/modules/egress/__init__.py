"""Egress module — temporary internet for lab gateways via Tailscale exit node.

Optional and default-off for the world. Enabled when config.yaml has a
`modules.egress` section; main.py builds the EgressEngine, registers it as a
status contributor, and mounts `router`.
"""

from app.modules.egress.engine import (
    MAX_DURATION,
    EgressConfig,
    EgressEngine,
    exit_node_active,
)
from app.modules.egress.routes import router

__all__ = ["MAX_DURATION", "EgressConfig", "EgressEngine", "exit_node_active", "router"]
