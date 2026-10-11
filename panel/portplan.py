import re

from ports import PORT_CONFIG, atomic_json, get_exit, reconcile, state


def _service_name(value):
    safe = re.sub(r'[^a-z0-9_-]', '-', str(value or 'custom').lower()).strip('-')[:24]
    return safe or 'custom'


def validate_plan(mappings, exclude_exit=None):
    cfg = state()
    existing = [m for m in cfg.get('mappings', [])
                if m.get('enabled', True) and m.get('exit_id') != exclude_exit]
    normalized = []
    occupied = {(m.get('proto'), int(m.get('listen_port', 0))): m for m in existing}

    for index, raw in enumerate(mappings or []):
        proto = str(raw.get('proto', '')).lower().strip()
        if proto not in {'tcp', 'udp'}:
            raise ValueError(f'ردیف {index + 1}: پروتکل باید TCP یا UDP باشد')
        listen = int(raw.get('listen_port', 0))
        target = int(raw.get('target_port', 0))
        if not 1 <= listen <= 65535:
            raise ValueError(f'ردیف {index + 1}: پورت ایران نامعتبر است')
        if not 1 <= target <= 65535:
            raise ValueError(f'ردیف {index + 1}: پورت خارج نامعتبر است')
        service = _service_name(raw.get('service'))
        key = (proto, listen)
        old = occupied.get(key)
        if old:
            owner = old.get('exit_id') or old.get('service') or 'mapping'
            raise ValueError(
                f'پورت {proto.upper()}/{listen} روی IP ایران قبلاً برای {owner} رزرو شده است'
            )
        item = {
            'service': service,
            'proto': proto,
            'listen_port': listen,
            'target_port': target,
            'port_mode': 'preserve' if listen == target else 'custom',
            'enabled': True,
        }
        occupied[key] = item
        normalized.append(item)

    return normalized


def apply_plan(exit_id, mappings):
    get_exit(exit_id)
    normalized = validate_plan(mappings, exclude_exit=exit_id)
    cfg = state()
    kept = [m for m in cfg.get('mappings', []) if m.get('exit_id') != exit_id]
    built = []
    for index, item in enumerate(normalized, start=1):
        built.append({
            'id': f"{exit_id}-{item['service']}-{item['proto']}-{item['listen_port']}-{index}",
            'exit_id': exit_id,
            **item,
        })
    cfg['mappings'] = kept + built
    atomic_json(PORT_CONFIG, cfg)
    reconcile()
    return built
