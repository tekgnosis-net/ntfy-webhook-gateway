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


def test_omada_preset_handles_all_payload_shapes():
    omada = get_preset("omada")
    wrapped = {"event": {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}}
    flat = {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}
    # Real controller alerts: description is constant boilerplate, the actual
    # event lines ride in the "text" list, and "Controller" names the device.
    real_alert = {"description": "This is a webhook message from Omada Controller",
                  "shardSecret": "x",
                  "text": ["The number of logs is about to reach the storage limit."],
                  "Controller": "Tekgnosis (OC200)", "timestamp": 1787071690833}
    # Omada's webhook-test message carries only description (+ secret).
    test_msg = {"description": "This is a webhook test message. Please ignore this",
                "shardSecret": "x"}
    for payload in (wrapped, flat):
        assert render(omada["message_template"], payload) == "went down"
        assert render(omada["title_template"], payload) == "Omada: Device"
        assert resolve_first(payload, omada["level_field"]) == "WARN"
    assert render(omada["message_template"], real_alert) == \
        "The number of logs is about to reach the storage limit."
    assert render(omada["title_template"], real_alert) == "Omada: Tekgnosis (OC200)"
    assert "test message" in render(omada["message_template"], test_msg)
    assert "WARN" in omada["rules"]
    assert omada["rules"]["ERROR"]["priority"] == "urgent"
