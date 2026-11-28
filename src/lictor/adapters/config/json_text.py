"""Set one member of a JSON object in its text, leaving every other byte as the person wrote it.

`lictor.json` is the person's file: they align its controls one per line, or split them, and a
command that records a fact in it (`lictor conventions --reviewed`) must not rewrite it in its own
form. The members are found with `json`'s own scanner, so what is changed is exactly the span of
one value, or one new member after the last one.
"""

import json
from dataclasses import dataclass

_DECODER = json.JSONDecoder()
_SPACE = " \t\n\r"


@dataclass(frozen=True)
class _Member:
    key: str
    key_start: int
    value_start: int
    value_end: int


def set_member(text: str, path: tuple[str, ...], value: object) -> str:
    """The text of a JSON object with `path` set to `value`, every other byte as it was.

    A missing object on the way is created with the rest of the path; a value that isn't an
    object on the way is replaced whole. Raises ValueError if `text` isn't a JSON object.
    """
    start = _skip(text, 0)
    if not text.startswith("{", start):
        raise ValueError("no es un objeto JSON")
    for depth, key in enumerate(path):
        members = _members(text, start)
        found = next((m for m in members if m.key == key), None)
        rest = _nested(path[depth + 1 :], value)
        if found is None:
            return _insert(text, start, members, key, rest)
        last = depth == len(path) - 1
        if last or not text.startswith("{", found.value_start):
            shown = _render(rest, _indent_at(text, found.key_start))
            return text[: found.value_start] + shown + text[found.value_end :]
        start = found.value_start
    return text


def _members(text: str, start: int) -> list[_Member]:
    """The members of the object whose `{` is at `start`, with where each key and value are."""
    members: list[_Member] = []
    at = _skip(text, start + 1)
    while not text.startswith("}", at):
        key, after_key = _DECODER.raw_decode(text, at)
        colon = _skip(text, after_key)
        value_start = _skip(text, colon + 1)
        _, value_end = _DECODER.raw_decode(text, value_start)
        members.append(_Member(str(key), at, value_start, value_end))
        at = _skip(text, value_end)
        if text.startswith(",", at):
            at = _skip(text, at + 1)
    return members


def _insert(text: str, start: int, members: list[_Member], key: str, value: object) -> str:
    """Add `"key": value` after the last member, on its own line if that one is on its own."""
    if not members:
        member = f"{json.dumps(key, ensure_ascii=False)}: {_render(value, _indent_at(text, start))}"
        return text[: start + 1] + member + text[start + 1 :]
    last = members[-1]
    indent = _indent_at(text, last.key_start)
    own_line = text[text.rfind("\n", 0, last.key_start) + 1 : last.key_start].strip() == ""
    gap = f",\n{indent}" if own_line else ", "
    member = f"{json.dumps(key, ensure_ascii=False)}: {_render(value, indent)}"
    return text[: last.value_end] + gap + member + text[last.value_end :]


def _nested(path: tuple[str, ...], value: object) -> object:
    """`value` inside an object for each key of `path`, the first one outermost."""
    for key in reversed(path):
        value = {key: value}
    return value


def _render(value: object, indent: str) -> str:
    """A value as `init` writes one, its lines indented as the line where its key starts."""
    return json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n" + indent)


def _indent_at(text: str, position: int) -> str:
    """The leading whitespace of the line where `position` is."""
    line = text[text.rfind("\n", 0, position) + 1 :]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _skip(text: str, at: int) -> int:
    while at < len(text) and text[at] in _SPACE:
        at += 1
    return at
