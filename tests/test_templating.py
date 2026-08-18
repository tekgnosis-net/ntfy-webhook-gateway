from app.templating import payload_text, render, resolve_first, resolve_path


def test_resolve_nested_path():
    assert resolve_path({"event": {"level": "WARN"}}, "event.level") == "WARN"


def test_resolve_list_index():
    assert resolve_path({"items": [{"x": 1}]}, "items.0.x") == 1


def test_resolve_missing_returns_none():
    assert resolve_path({"a": 1}, "a.b.c") is None


def test_resolve_first_takes_first_hit():
    assert resolve_first({"text": "hi"}, "event.text|text") == "hi"
    assert resolve_first({"event": {"text": "in"}}, "event.text|text") == "in"


def test_render_substitutes_and_blanks_missing():
    out = render("[{cat}] {who}: {msg}", {"cat": "System", "msg": "up"})
    assert out == "[System] : up"


def test_render_payload_pretty_prints_json():
    assert '"a": 1' in render("{payload}", {"a": 1})


def test_render_payload_raw_body():
    assert render("{payload}", {"body": "plain text"}) == "plain text"


def test_render_empty_template():
    assert render("", {"a": 1}) == ""
    assert render(None, {"a": 1}) == ""


def test_render_joins_list_values():
    # Omada's legacy "text" field is a list of event lines.
    assert render("{text}", {"text": ["AP1 down", "AP2 up"]}) == "AP1 down\nAP2 up"
