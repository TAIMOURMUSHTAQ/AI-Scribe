import json

from scribe.partial_json import parse_partial

DOC = {"analysis": {"summary": "Child did well \"today\" — 70%", "findings": [{"id": "F1", "source": "measured", "basis": ["a", "b"]}],
                    "n": 12.5, "ok": True, "none": None},
       "soap": {"subjective": [{"text": "ammi ne bataya", "basis": []}]}}


def test_every_prefix_parses_to_a_dict_and_full_text_round_trips():
    text = json.dumps(DOC, ensure_ascii=False)
    last = None
    for i in range(1, len(text) + 1):
        r = parse_partial(text[:i])
        assert r is None or isinstance(r, dict)
        if r is not None:
            last = r
    assert last == DOC


def test_value_strings_stream_but_keys_do_not():
    assert parse_partial('{"summary": "Child did we') == {"summary": "Child did we"}
    assert parse_partial('{"a": "x", "summ') == {"a": "x"}
    assert parse_partial('{"a": [{"id": "F1", "te') == {"a": [{"id": "F1"}]}
