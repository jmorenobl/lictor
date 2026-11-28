"""Setting one member of a JSON object in its text, every other byte as it was."""

import json

import pytest

from lictor.adapters.config.json_text import set_member


@pytest.mark.parametrize(
    ("text", "path", "value", "expected"),
    [
        # A value replaced in its place, the line it is on and the rest untouched.
        ('{"a": 1,  "b": {"c": 2}}', ("b", "c"), 3, '{"a": 1,  "b": {"c": 3}}'),
        # A member added after the last one: inline after an inline one.
        ('{"a": 1}', ("b",), "x", '{"a": 1, "b": "x"}'),
        # On its own line, with that line's indent, after one on its own line.
        ('{\n    "a": 1\n}\n', ("b",), [1], '{\n    "a": 1,\n    "b": [\n      1\n    ]\n}\n'),
        # The objects missing on the way are created with the rest of the path.
        ('{"a": 1}', ("b", "c"), 2, '{"a": 1, "b": {\n  "c": 2\n}}'),
        # A value on the way that isn't an object is replaced whole.
        ('{"b": 5}', ("b", "c"), 2, '{"b": {\n  "c": 2\n}}'),
        # In an empty object, the first member.
        ("{}", ("a",), "ñ", '{"a": "ñ"}'),
        # Keys with escapes and nested values with commas are read by `json` itself.
        ('{"x\\"y": [1, {"z": ","}], "k": 0}', ("k",), 1, '{"x\\"y": [1, {"z": ","}], "k": 1}'),
    ],
)
def test_one_member_is_set_and_nothing_else_changes(
    text: str, path: tuple[str, ...], value: object, expected: str
) -> None:
    told = set_member(text, path, value)

    assert told == expected
    assert json.loads(told) is not None


def test_a_text_that_isnt_an_object_is_refused() -> None:
    with pytest.raises(ValueError, match="no es un objeto JSON"):
        set_member("[1]", ("a",), 1)
