import json
import re

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.|\-]+)\}")


def resolve_path(data, path: str):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def resolve_first(data, spec: str):
    for path in spec.split("|"):
        value = resolve_path(data, path.strip())
        if value is not None:
            return value
    return None


def payload_text(event: dict) -> str:
    if set(event.keys()) == {"body"}:
        return str(event["body"])
    return json.dumps(event, indent=2, ensure_ascii=False, default=str)


def render(template: str | None, event: dict) -> str:
    def substitute(match: re.Match) -> str:
        spec = match.group(1)
        if spec == "payload":
            return payload_text(event)
        value = resolve_first(event, spec)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(substitute, template or "").strip()
