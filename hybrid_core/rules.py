from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re

from .services import ServiceForwardingSpec


_IFACE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,15}")


@dataclass(frozen=True)
class RuleCommand:
    table: str
    chain: str
    args: tuple[str, ...]

    def as_iptables_args(self, action: str = "-A") -> tuple[str, ...]:
        if action not in {"-A", "-I", "-D", "-C"}:
            raise ValueError("unsupported iptables action")
        return ("iptables", "-w", "5", "-t", self.table, action, self.chain, *self.args)


@dataclass(frozen=True)
class CutoverRulePlan:
    service: str
    target_ip: str
    source_ip: str
    output_interface: str
    add_rules: tuple[RuleCommand, ...]
    remove_rules: tuple[RuleCommand, ...] = ()

    def add_commands(self) -> tuple[tuple[str, ...], ...]:
        return tuple(rule.as_iptables_args("-A") for rule in self.add_rules)

    def remove_commands(self) -> tuple[tuple[str, ...], ...]:
        return tuple(rule.as_iptables_args("-D") for rule in reversed(self.add_rules))


def _valid_ip(value: str) -> str:
    ip = ipaddress.ip_address(str(value).strip())
    if ip.version != 4:
        raise ValueError("TP27 cutover rule renderer currently requires IPv4")
    return str(ip)


def _valid_iface(value: str) -> str:
    value = str(value).strip()
    if not _IFACE_RE.fullmatch(value):
        raise ValueError("invalid interface name")
    return value


def _service_name(spec: ServiceForwardingSpec) -> str:
    return spec.protocol.value


def build_cutover_rules(
    spec: ServiceForwardingSpec,
    *,
    target_ip: str,
    source_ip: str,
    output_interface: str,
    prerouting_chain: str = "TP27_PRE",
    forward_chain: str = "TP27_FWD",
    postrouting_chain: str = "TP27_POST",
    include_native_ip_protocols: bool = False,
) -> CutoverRulePlan:
    target = _valid_ip(target_ip)
    source = _valid_ip(source_ip)
    iface = _valid_iface(output_interface)
    for chain in (prerouting_chain, forward_chain, postrouting_chain):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,28}", chain):
            raise ValueError("invalid iptables chain")

    rules: list[RuleCommand] = []
    # Return traffic needs one stateful rule per overlay interface rather than a
    # copy for every service port.
    rules.append(
        RuleCommand(
            "filter",
            forward_chain,
            (
                "-i",
                iface,
                "-m",
                "conntrack",
                "--ctstate",
                "RELATED,ESTABLISHED",
                "-j",
                "ACCEPT",
            ),
        )
    )

    for mapping in spec.mappings:
        proto = mapping.proto
        listen = str(int(mapping.listen_port))
        target_port = str(int(mapping.target_port))
        rules.extend(
            [
                RuleCommand(
                    "nat",
                    prerouting_chain,
                    (
                        "-p",
                        proto,
                        "--dport",
                        listen,
                        "-j",
                        "DNAT",
                        "--to-destination",
                        f"{target}:{target_port}",
                    ),
                ),
                RuleCommand(
                    "filter",
                    forward_chain,
                    (
                        "-d",
                        f"{target}/32",
                        "-o",
                        iface,
                        "-p",
                        proto,
                        "--dport",
                        target_port,
                        "-j",
                        "ACCEPT",
                    ),
                ),
                RuleCommand(
                    "nat",
                    postrouting_chain,
                    (
                        "-d",
                        f"{target}/32",
                        "-o",
                        iface,
                        "-p",
                        proto,
                        "--dport",
                        target_port,
                        "-j",
                        "SNAT",
                        "--to-source",
                        source,
                    ),
                ),
            ]
        )

    if include_native_ip_protocols:
        for ipproto in spec.ip_protocols:
            proto = str(int(ipproto))
            rules.extend(
                [
                    RuleCommand(
                        "nat",
                        prerouting_chain,
                        ("-p", proto, "-j", "DNAT", "--to-destination", target),
                    ),
                    RuleCommand(
                        "filter",
                        forward_chain,
                        ("-d", f"{target}/32", "-o", iface, "-p", proto, "-j", "ACCEPT"),
                    ),
                    RuleCommand(
                        "nat",
                        postrouting_chain,
                        (
                            "-d",
                            f"{target}/32",
                            "-o",
                            iface,
                            "-p",
                            proto,
                            "-j",
                            "SNAT",
                            "--to-source",
                            source,
                        ),
                    ),
                ]
            )

    return CutoverRulePlan(
        service=_service_name(spec),
        target_ip=target,
        source_ip=source,
        output_interface=iface,
        add_rules=tuple(rules),
    )


def rule_plan_dict(plan: CutoverRulePlan) -> dict:
    return {
        "service": plan.service,
        "target_ip": plan.target_ip,
        "source_ip": plan.source_ip,
        "output_interface": plan.output_interface,
        "add": [list(cmd) for cmd in plan.add_commands()],
        "rollback_remove": [list(cmd) for cmd in plan.remove_commands()],
    }
