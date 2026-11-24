"""The backend's own span arithmetic for content edits, and minimal edit operations.

The adjust functions are the library backend's (openpecha-backend
database/span_database.py, commit cd1c205, 2026-09-04, lines 11-101), copied verbatim so
that checking "does this content edit keep every segment boundary?" replays the server,
not a re-reading of it. A segment line span is a "continuous" span.
"""
from __future__ import annotations

import difflib


def _adjust_continuous_for_insert(start, end, insert_pos, insert_len):
    if insert_pos == 0 and start == 0:
        return (start, end + insert_len)
    if insert_pos <= start:
        return (start + insert_len, end + insert_len)
    if insert_pos <= end:
        return (start, end + insert_len)
    return (start, end)


def _adjust_span_for_delete(start, end, del_start, del_end):
    del_len = del_end - del_start
    if del_end <= start:
        return (start - del_len, end - del_len)
    if del_start >= end:
        return (start, end)
    if del_start <= start and del_end >= end:
        return None
    if del_start <= start < del_end < end:
        return (del_start, end - del_len)
    if start < del_start < end <= del_end:
        return (start, del_start)
    if start < del_start and del_end < end:
        return (start, end - del_len)
    return (start, end)


def _adjust_continuous_for_replace(start, end, replace_start, replace_end, new_len, is_first_encompassed):
    delta = new_len - (replace_end - replace_start)
    if replace_start >= end:
        return (start, end)
    if replace_end <= start:
        return (start + delta, end + delta)
    if start == replace_start and end == replace_end:
        return (start, start + new_len)
    if replace_start <= start and replace_end >= end:
        if is_first_encompassed:
            return (replace_start, replace_start + new_len)
        return None
    if start < replace_start and replace_end < end:
        return (start, end + delta)
    if replace_start <= start < replace_end < end:
        return (replace_start + new_len, end + delta)
    if start < replace_start < end <= replace_end:
        return (start, replace_start + new_len)
    return (start, end)


def apply_op_to_spans(spans, op):
    """Replay one operation over (start, end) spans in the backend's order (spans are fetched
    ORDER BY span_start, so `is_first_encompassed` goes to the lowest-start encompassed span).
    None entries are spans the backend would delete."""
    out = []
    if op["type"] == "insert":
        for s in spans:
            out.append(None if s is None else _adjust_continuous_for_insert(s[0], s[1], op["position"], len(op["text"])))
    elif op["type"] == "delete":
        for s in spans:
            out.append(None if s is None else _adjust_span_for_delete(s[0], s[1], op["start"], op["end"]))
    else:
        first_found = False
        order = sorted((i for i, s in enumerate(spans) if s is not None), key=lambda i: (spans[i][0], spans[i][1]))
        res = {}
        for i in order:
            s = spans[i]
            encompassed = op["start"] <= s[0] and op["end"] >= s[1]
            is_first = encompassed and not first_found
            if is_first:
                first_found = True
            res[i] = _adjust_continuous_for_replace(s[0], s[1], op["start"], op["end"], len(op["text"]), is_first)
        out = [res.get(i) if spans[i] is not None else None for i in range(len(spans))]
    return out


def apply_op_to_text(text, op):
    if op["type"] == "insert":
        return text[:op["position"]] + op["text"] + text[op["position"]:]
    if op["type"] == "delete":
        return text[:op["start"]] + text[op["end"]:]
    return text[:op["start"]] + op["text"] + text[op["end"]:]


MAX_DIFF = 60_000      # chars per side above which difflib (quadratic) is not run


def make_ops(old, new):
    """Minimal insert/delete/replace ops taking `old` to `new`, positions stated against `old`,
    highest position first so they can be sent in order without re-reading the content.

    The common start and end are trimmed first; only the middle is diffed. If the middle is
    still larger than MAX_DIFF on either side, it becomes one replace — a valid edit whose
    effect on the segment boundaries is replayed like any other (usually: re-segment)."""
    a = 0
    lim = min(len(old), len(new))
    while a < lim and old[a] == new[a]:
        a += 1
    z = 0
    while z < lim - a and old[len(old) - 1 - z] == new[len(new) - 1 - z]:
        z += 1
    o_mid, n_mid = old[a:len(old) - z], new[a:len(new) - z]
    if not o_mid and not n_mid:
        return []
    if len(o_mid) > MAX_DIFF or len(n_mid) > MAX_DIFF:
        if not o_mid:
            return [{"type": "insert", "position": a, "text": n_mid}]
        if not n_mid:
            return [{"type": "delete", "start": a, "end": a + len(o_mid)}]
        return [{"type": "replace", "start": a, "end": a + len(o_mid), "text": n_mid}]
    sm = difflib.SequenceMatcher(None, o_mid, n_mid, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        i1, i2 = i1 + a, i2 + a
        if tag == "equal":
            continue
        if tag == "insert":
            ops.append({"type": "insert", "position": i1, "text": n_mid[j1:j2]})
        elif tag == "delete":
            ops.append({"type": "delete", "start": i1, "end": i2})
        else:
            ops.append({"type": "replace", "start": i1, "end": i2, "text": n_mid[j1:j2]})
    ops.reverse()
    return ops
