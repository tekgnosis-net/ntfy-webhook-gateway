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


def _stringify(value) -> str:
    # Omada's legacy "text" field is a list of event lines; render lists as
    # one line each rather than a Python repr.
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


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
        return "" if value is None else _stringify(value)

    return _PLACEHOLDER.sub(substitute, template or "").strip()


def render_strict(template: str | None, event: dict) -> str:
    """Like render(), but a template whose placeholders ALL come up empty
    renders as "" even when it contains literal text — so callers' fallbacks
    fire instead of sending punctuation-only skeletons like "[] :".
    Templates with no placeholders at all pass through unchanged."""
    resolved = 0

    def substitute(match: re.Match) -> str:
        nonlocal resolved
        spec = match.group(1)
        if spec == "payload":
            text = payload_text(event)
        else:
            value = resolve_first(event, spec)
            text = "" if value is None else _stringify(value)
        if text.strip():
            resolved += 1
        return text

    out = _PLACEHOLDER.sub(substitute, template or "").strip()
    if _PLACEHOLDER.search(template or "") and not resolved:
        return ""
    return out
