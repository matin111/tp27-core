from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HealthPolicy:
    failure_threshold: int = 4
    recovery_threshold: int = 1
    cooldown_seconds: int = 300

    def __post_init__(self):
        if self.failure_threshold < 2:
            raise ValueError('failure_threshold must be >= 2')
        if self.recovery_threshold < 1:
            raise ValueError('recovery_threshold must be >= 1')
        if self.cooldown_seconds < 0:
            raise ValueError('cooldown_seconds must be >= 0')


@dataclass(frozen=True)
class HealthSample:
    ts: float
    service_active: bool
    peer_ok: bool
    rx_progress: bool
    agent_online: bool

    @property
    def alive(self) -> bool:
        return self.service_active and (self.peer_ok or self.rx_progress)

    @property
    def reason(self) -> str:
        if not self.service_active:
            return 'SERVICE_DOWN'
        if self.peer_ok:
            return 'PEER_OK'
        if self.rx_progress:
            return 'RX_PROGRESS'
        if not self.agent_online:
            return 'UNDERLAY_CONTROL_DOWN'
        return 'PEER_DOWN_RX_STALLED'


def new_state() -> dict[str, Any]:
    return {
        'phase': 'healthy',
        'failure_streak': 0,
        'recovery_streak': 0,
        'last_switch': 0.0,
        'last_reason': 'INIT',
    }


def evaluate(state: dict[str, Any] | None, sample: HealthSample,
             policy: HealthPolicy) -> tuple[dict[str, Any], str]:
    """Evaluate one observation without mutating the caller's state.

    Actions:
      hold     - no topology change.
      prepare  - current path is persistently unhealthy and the Exit control
                 path is alive, so a make-before-break candidate may be built.

    Peer ICMP alone is not authoritative: increasing RX bytes counts as alive.
    This prevents false failover while the data plane still carries traffic.
    """
    out = dict(new_state())
    if state:
        out.update(state)

    out['last_reason'] = sample.reason

    if sample.alive:
        out['failure_streak'] = 0
        out['recovery_streak'] = int(out.get('recovery_streak') or 0) + 1
        if out['recovery_streak'] >= policy.recovery_threshold:
            out['phase'] = 'healthy'
        return out, 'hold'

    out['recovery_streak'] = 0
    out['failure_streak'] = int(out.get('failure_streak') or 0) + 1
    out['phase'] = 'degraded'

    if out['failure_streak'] < policy.failure_threshold:
        return out, 'hold'

    # If the Agent/control path is down, changing transport on the same Exit
    # cannot be provisioned safely. Keep the current interface instead.
    if not sample.agent_online:
        out['phase'] = 'underlay-down'
        return out, 'hold'

    last_switch = float(out.get('last_switch') or 0.0)
    if sample.ts - last_switch < policy.cooldown_seconds:
        out['phase'] = 'cooldown'
        return out, 'hold'

    out['phase'] = 'candidate-needed'
    return out, 'prepare'
