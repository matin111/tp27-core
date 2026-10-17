#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hybrid_core import (  # noqa: E402
    LinkMetrics,
    PolicyOptions,
    ServiceProtocol,
    TransportKind,
    build_all_protocol_plans,
    build_plan,
)


def _load_metrics(path: str | None) -> dict[TransportKind, LinkMetrics]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("metrics JSON must be an object keyed by transport name")
    out: dict[TransportKind, LinkMetrics] = {}
    for name, values in raw.items():
        kind = TransportKind(name)
        if not isinstance(values, dict):
            raise ValueError(f"metrics for {name} must be an object")
        out[kind] = LinkMetrics(
            healthy=values.get("healthy", True),
            rtt_ms=values.get("rtt_ms", 0),
            jitter_ms=values.get("jitter_ms", 0),
            loss_pct=values.get("loss_pct", 0),
            throughput_mbps=values.get("throughput_mbps", 0),
            recent_failures=values.get("recent_failures", 0),
        )
    return out


def _options(args: argparse.Namespace) -> PolicyOptions:
    blocked = frozenset(TransportKind(x) for x in (args.block or []))
    return PolicyOptions(
        require_encryption=not args.allow_plain,
        allow_experimental=args.allow_experimental,
        prefer_outer_udp=not args.prefer_tcp,
        max_fallbacks=args.max_fallbacks,
        blocked_transports=blocked,
    )


def _plan_dict(plan):
    return {
        "protocol": plan.protocol.value,
        "primary": plan.primary.value if plan.primary else None,
        "fallbacks": [x.value for x in plan.fallbacks],
        "rationale": list(plan.rationale),
        "ranked": [
            {
                "transport": row.transport.value,
                "compatible": row.compatible,
                "score": row.score,
                "reasons": list(row.reasons),
            }
            for row in plan.ranked
        ],
    }


def cmd_plan(args: argparse.Namespace) -> int:
    metrics = _load_metrics(args.metrics)
    options = _options(args)
    if args.protocol == "all":
        plans = build_all_protocol_plans(metrics=metrics, options=options)
        payload = {k.value: _plan_dict(v) for k, v in plans.items()}
    else:
        plan = build_plan(ServiceProtocol(args.protocol), metrics=metrics, options=options)
        payload = _plan_dict(plan)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tp27-hybridctl",
        description="Offline TP27 Hybrid Core policy planner. It never changes the host network.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="rank transports for one or all VPN service protocols")
    plan.add_argument(
        "--protocol",
        default="all",
        choices=["all"] + [p.value for p in ServiceProtocol],
    )
    plan.add_argument("--metrics", help="JSON file containing measured link metrics")
    plan.add_argument("--allow-plain", action="store_true", help="allow unencrypted carriers such as direct GRE")
    plan.add_argument("--allow-experimental", action="store_true")
    plan.add_argument("--prefer-tcp", action="store_true", help="do not give outer-UDP transports the preference bonus")
    plan.add_argument("--max-fallbacks", type=int, default=3)
    plan.add_argument(
        "--block",
        action="append",
        choices=[x.value for x in TransportKind],
        help="exclude a transport; can be repeated",
    )
    plan.set_defaults(func=cmd_plan)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
