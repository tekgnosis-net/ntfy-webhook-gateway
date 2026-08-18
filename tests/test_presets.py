from app.presets import PRESETS, get_preset
from app.templating import render, resolve_first


def test_preset_keys_complete():
    required = {"key", "label", "description", "title_template", "message_template",
                "level_field", "rules", "default_priority", "tags"}
    for preset in PRESETS:
        assert required <= set(preset), preset.get("key")


def test_get_preset_returns_copy():
    omada = get_preset("omada")
    omada["tags"] = "mutated"
    assert get_preset("omada")["tags"] == "omada,network"
    assert get_preset("nope") is None


def test_omada_preset_handles_both_payload_shapes():
    omada = get_preset("omada")
    wrapped = {"event": {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}}
    flat = {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}
    for payload in (wrapped, flat):
        assert render(omada["message_template"], payload) == "[Device] AP1: went down"
        assert resolve_first(payload, omada["level_field"]) == "WARN"
    assert "WARN" in omada["rules"]
    assert omada["rules"]["ERROR"]["priority"] == "urgent"
