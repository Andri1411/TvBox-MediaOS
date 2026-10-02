from tvbox import audio
from tvbox.hub import sway_safe


def test_parse_volume():
    assert audio.parse_volume("Volume: 0.45\n") == (45, False)
    assert audio.parse_volume("Volume: 1.00 [MUTED]\n") == (100, True)
    assert audio.parse_volume("Volume: 0.07") == (7, False)
    assert audio.parse_volume("") is None


def test_parse_sinks():
    node = "PipeWire:Interface:Node"
    dump = [
        {"id": 31, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
         "metadata": [{"key": "default.audio.sink", "value": {"name": "alsa_output.hdmi"}},
                      {"key": "default.audio.source", "value": {"name": "x"}}]},
        {"id": 50, "type": node, "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "bluez_output.aa", "node.description": "Headphones"}}},
        {"id": 48, "type": node, "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "alsa_output.hdmi", "node.description": "HDMI / TV"}}},
        {"id": 49, "type": node, "info": {"props": {"media.class": "Audio/Source", "node.name": "mic"}}},
        {"id": 51, "type": node, "info": None},
    ]
    assert audio.parse_sinks(dump) == [
        {"id": 48, "name": "HDMI / TV", "default": True},
        {"id": 50, "name": "Headphones", "default": False},
    ]
    assert audio.parse_sinks([]) == []


def test_workspace_names_cannot_inject_sway_commands():
    assert sway_safe("youtube") and sway_safe("disney-plus_2")
    for bad in ("", "a b", "x; exec foot", "x,exit", 'a"b'):
        assert not sway_safe(bad)
