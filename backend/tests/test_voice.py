from atlas.core.voice import spoken_text


def test_spoken_text_removes_visual_markup() -> None:
    assert spoken_text("See [the study](https://example.com) for details.") == "See the study for details."


def test_spoken_text_rejects_structured_tool_output() -> None:
    assert spoken_text('{"tool_call":{"name":"exa_search","arguments":{}}}') == ""
