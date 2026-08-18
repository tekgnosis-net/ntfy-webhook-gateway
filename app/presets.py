import copy

_URGENT = {"priority": "urgent", "extra_tags": ["rotating_light", "fire"]}
_HIGH = {"priority": "high", "extra_tags": ["warning"]}
_INFO = {"priority": "default", "extra_tags": ["information_source"]}

PRESETS = [
    {
        "key": "generic",
        "label": "Generic JSON",
        "description": "Sends the whole payload pretty-printed; the title falls back to the endpoint name.",
        "title_template": "",
        "message_template": "{payload}",
        "level_field": "",
        "rules": {},
        "default_priority": "default",
        "tags": "webhook",
    },
    {
        "key": "omada",
        "label": "TP-Link Omada",
        "description": "Reproduces the original Omada controller mapping (wrapped or flat payloads).",
        "title_template": "Omada: {event.category|category} ({event.level|level})",
        "message_template": "[{event.category|category}] {event.target|target}: {event.text|text}",
        "level_field": "event.level|level",
        "rules": {
            "WARN": _HIGH, "WARNING": _HIGH,
            "ALERT": _URGENT, "ERROR": _URGENT, "CRITICAL": _URGENT,
            "NOTICE": _INFO, "INFO": _INFO,
        },
        "default_priority": "default",
        "tags": "omada,network",
    },
]


def get_preset(key: str) -> dict | None:
    preset = next((p for p in PRESETS if p["key"] == key), None)
    return copy.deepcopy(preset) if preset else None
