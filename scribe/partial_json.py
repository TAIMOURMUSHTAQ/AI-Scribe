"""Parse an incomplete JSON document (a prefix of a streamed model reply).

Used to show the clinical analysis and SOAP note on screen *while* the model is still
writing them. Returns the largest valid object that the prefix implies: open strings
that are values are shown as typed so far; half-written keys, numbers and literals
are dropped until complete.
"""
from __future__ import annotations

import json
from typing import Any


def parse_partial(text: str) -> Any:
    text = text.lstrip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stack: list[str] = []          # '{' or '['
    expect_key: list[bool] = []    # per level: True if the next string in this object is a key
    safe: list[tuple[int, str]] = []  # (cut index, closers) where text[:i] + closers is valid
    in_string = False
    string_is_value = False
    escape = False
    i = 0
    n = len(text)

    def closers() -> str:
        return "".join("}" if c == "{" else "]" for c in reversed(stack))

    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                if string_is_value:
                    safe.append((i + 1, closers()))
                elif stack and stack[-1] == "{":
                    expect_key[-1] = False  # key done, value comes next
            i += 1
            continue

        if ch == '"':
            in_string = True
            string_is_value = not (stack and stack[-1] == "{" and expect_key[-1])
        elif ch in "{[":
            stack.append(ch)
            expect_key.append(ch == "{")
            safe.append((i + 1, closers()))
        elif ch in "}]":
            if stack:
                stack.pop()
                expect_key.pop()
            safe.append((i + 1, closers()))
        elif ch == ",":
            safe.append((i, closers()))
            if stack and stack[-1] == "{":
                expect_key[-1] = True
        i += 1

    candidates: list[str] = []
    if in_string and string_is_value:
        body = text
        if escape:
            body = body[:-1]
        # drop a dangling partial unicode escape such as \u00
        tail = body[-6:]
        k = tail.rfind("\\u")
        if k != -1 and len(tail) - k < 6:
            body = body[: len(body) - (len(tail) - k)]
        candidates.append(body + '"' + closers())
    for idx, close in reversed(safe):
        candidates.append(text[:idx] + close)

    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None
