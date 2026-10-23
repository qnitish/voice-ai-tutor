"""Whiteboard blocks: valid ones pass clean_draw with defaults filled in, malformed ones are dropped."""

from app.schemas import clean_draw


def test_blocks_are_kept_with_defaults():
    out = clean_draw(
        [
            {"type": "wire", "id": "w1", "start": [900, 200], "end": [900, 600], "current": "I"},
            {"type": "charge", "id": "q1", "x": 1100, "y": 400, "velocity_deg": 0},
            {"type": "compass", "id": "k1", "x": 1200, "y": 300},
            {"type": "magnet", "id": "m1", "x": 900, "y": 700},
            {"type": "axes", "id": "x1", "x": 860, "y": 600},
            {"type": "region", "id": "r1", "cx": 1200, "cy": 450, "w": 300, "h": 200, "orientation": "ccw"},
            {"type": "flow", "id": "s1", "x": 860, "y": 200, "items": ["CH₄", "C₂H₆"], "connector": "+CH₂"},
            {"type": "chain", "id": "c1", "x": 860, "y": 300, "groups": ["CH₃", "CH₂", "OH"],
             "branches": [{"at": 1, "groups": ["CH₃"]}]},
            {"type": "ring", "id": "b1", "cx": 1200, "cy": 500, "aromatic": True},
            {"type": "curve", "id": "g1", "points": [[860, 600], [960, 500], [1060, 300]], "label": "y = x²"},
        ]
    )
    assert [a["type"] for a in out] == [
        "wire", "charge", "compass", "magnet", "axes", "region", "flow", "chain", "ring", "curve",
    ]
    charge = out[1]
    assert charge["sign"] == "+" and charge["force_deg"] is None and charge["color"] == "blue"
    assert out[7]["branches"][0]["dir"] == "up" and out[7]["bonds"] == []
    assert out[8]["size"] == 6


def test_malformed_blocks_are_dropped():
    out = clean_draw(
        [
            {"type": "flow", "id": "s1", "x": 0, "y": 0, "items": []},  # no items
            {"type": "chain", "id": "c1", "x": 0, "y": 0, "groups": ["C"], "bonds": ["~"]},  # unknown bond
            {"type": "curve", "id": "g1", "points": [[0, 0]]},  # one point
            {"type": "ring", "id": "b1", "cx": 0, "cy": 0, "size": 8},  # unsupported ring size
            {"type": "text", "id": "t1", "x": 60, "y": 90, "text": "kept"},
        ]
    )
    assert [a["id"] for a in out] == ["t1"]
