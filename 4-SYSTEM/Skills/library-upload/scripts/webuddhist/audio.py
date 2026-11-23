"""Audio on one library environment: status, push, update, replace, delete, verify.

Every note in 1-SOURCES/Audio is first linted (linter-audio, with the live edition
of the chosen environment) and compared with the ledger and with what the
environment actually holds. That comparison gives each note its state(s):

  ok                 everything on the environment matches the note
  new                not on this environment yet                    -> push
  partial            audio created, no recording                    -> push --only recording
  cues-pending       timed Mapping rows, no cues on the recording    -> push --only cues
  cues-changed       the cues on the recording differ from the note  -> push --only cues
  cues-extra         cues on the recording, none in the note         -> delete --only cues
  file-changed       the local file is not the one uploaded         -> replace
  audio-details      title / language / license / narrator differ   -> update --only audio
  recording-details  recording title or duration differ             -> update --only recording
  recording-edition  recording follows another edition than the root text's -> update --only recording
  blocked            lint errors: fix the note (nothing is sent)
  text-not-on-env    the root text does not exist on this environment (e.g. created on prod only)
  twin               another note for the same text and artist is the uploaded one
  gone               the ledger names an id this environment does not have  -> verify
  extra-recordings   the audio has recordings the ledger does not know      -> verify
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .api import ApiError, items
from .plan import NotePlan, Step
from .vault import LEDGERS, VAULT

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linter-audio"))
import audio_note as an  # noqa: E402
import lint_audio as la  # noqa: E402

STEPS = ("audio", "recording", "cues")
FIX = {
    "new": "push", "partial": "push --only recording", "cues-pending": "push --only cues",
    "cues-changed": "push --only cues", "cues-extra": "delete --only cues", "file-changed": "replace",
    "audio-details": "update --only audio", "recording-details": "update --only recording",
    "recording-edition": "update --only recording", "blocked": "fix the note: see the lint errors",
    "text-not-on-env": "upload the root text to this environment first",
    "twin": "nothing: the other note is (or will be) the uploaded one", "gone": "check by hand (verify)",
    "extra-recordings": "check by hand (verify)",
}
# states a write command never acts on
NO_WRITE = ("blocked", "twin", "gone", "text-not-on-env")


def note_key(path) -> tuple:
    """(text folder, note file name): the same note whether read from 1-SOURCES/Audio or a copy in Audio/."""
    p = Path(path)
    folder = p.parent.parent if p.parent.name == "audio" else p.parent
    return folder.name, p.name


def folder_name(rel) -> str:
    return note_key(rel)[0]


@dataclass
class Ctx:
    env: object
    client: object
    ledger: object
    seen: dict = field(default_factory=dict)
    texts: dict = field(default_factory=dict)       # text_id -> exists on env

    def text_exists(self, text_id) -> bool:
        if text_id not in self.texts:
            self.texts[text_id] = self.client.get_or_none(f"/v2/texts/{text_id}") is not None
        return self.texts[text_id]


@dataclass
class State:
    n: object
    rel: Path
    res: dict
    flags: dict = field(default_factory=dict)       # state -> detail
    entry: dict | None = None
    live_audio: dict | None = None
    live_rec: dict | None = None
    live_cues: list | None = None

    @property
    def key(self):
        return self.n.stem

    @property
    def rec(self):
        return self.res["recordings"][0] if len(self.res["recordings"]) == 1 else None

    @property
    def link(self):
        return la._note_link(self.rel)

    @property
    def title(self):
        return folder_name(self.rel) + "  ·  " + self.rel.name


# ------------------------------------------------------------------ assess

def _cue_tuples(cues) -> list:
    return sorted((c["start_ms"], c["end_ms"], c["span"]["start"], c["span"]["end"]) for c in cues or [])


def _audio_diff(live: dict, want: dict) -> dict:
    """Fields of `want` (the note's audio_input) whose value differs on the environment."""
    out = {}
    for k in ("title", "language", "license"):
        if k in want and want[k] != live.get(k):
            out[k] = want[k]
    keys = sorted({k for c in want.get("contributions", []) for k in c})
    norm = lambda cs: sorted(tuple((k, c.get(k)) for k in keys) for c in cs or [])  # noqa: E731
    if norm(want.get("contributions")) != norm(live.get("contributions")):
        out["contributions"] = want.get("contributions")
    return out


def _rec_diff(live: dict, meta: dict) -> dict:
    return {k: meta[k] for k in ("title", "duration_ms") if k in meta and meta[k] != live.get(k)}


def assess(n, ctx: Ctx) -> State:
    rel = n.path.relative_to(VAULT)
    res = la.lint(n, live_host=ctx.env.name, do_hash=True, require_file=False, seen_files=ctx.seen)
    if len(res["recordings"]) > 1:
        res["errors"].append({"code": "recordings", "message": f"{len(res['recordings'])} recordings: "
                              "one recording per note is supported"})
    st = State(n=n, rel=rel, res=res)
    f = st.flags
    if res["errors"]:
        f["blocked"] = "; ".join(f"[{e['code']}] {e['message']}" for e in res["errors"])
    entry = ctx.ledger.get(n.stem)
    if entry and entry.get("note") and note_key(entry["note"]) != note_key(rel):
        f["twin"] = f"same text and artist as {entry['note']}, which is the uploaded one"
        return st
    st.entry = entry
    if not entry or not entry.get("audio_id"):
        if "blocked" not in f:
            if res["text_id"] and not ctx.text_exists(res["text_id"]):
                f["text-not-on-env"] = f"text {res['text_id']} is not on {ctx.env.name}"
            else:
                f["new"] = f"not on {ctx.env.name}"
        return st

    st.live_audio = live_a = ctx.client.get_or_none(f"/v2/audios/{entry['audio_id']}")
    if live_a is None:
        f["gone"] = f"audio {entry['audio_id']} is in the ledger but not on {ctx.env.name}"
        return st
    diff = _audio_diff(live_a, res["audio_input"])
    if diff:
        f["audio-details"] = "differs: " + ", ".join(diff)
    if not entry.get("recording_id"):
        f["partial"] = f"audio {entry['audio_id']} has no recording from this note"
        return st
    others = [r for r in live_a.get("recordings", []) if r != entry["recording_id"]]
    if others:
        f["extra-recordings"] = f"the audio also has recordings {others}"
    st.live_rec = live_r = ctx.client.get_or_none(f"/v2/recordings/{entry['recording_id']}")
    if live_r is None:
        f["gone"] = f"recording {entry['recording_id']} is in the ledger but not on {ctx.env.name}"
        return st
    rec = st.rec
    if rec and rec.get("sha256") and entry.get("sha256") and rec["sha256"] != entry["sha256"]:
        f["file-changed"] = f"{Path(rec['file']).name} is not the file uploaded ({entry['sha256'][:10]}…)"
    if rec:
        rdiff = _rec_diff(live_r, rec["metadata"])
        if rdiff:
            f["recording-details"] = "differs: " + ", ".join(rdiff)
    if res["edition_id"] and live_r.get("edition_id") != res["edition_id"]:
        f["recording-edition"] = f"follows {live_r.get('edition_id')}, the root text's edition is {res['edition_id']}"
    st.live_cues = items(ctx.client.get(f"/v2/recordings/{entry['recording_id']}/cues"))
    if "blocked" not in f:
        want, have = _cue_tuples(res["cues"]), _cue_tuples(st.live_cues)
        if want and not have:
            f["cues-pending"] = f"{len(want)} cues in the note, none on {ctx.env.name}"
        elif want and want != have:
            f["cues-changed"] = f"{len(want)} cues in the note, {len(have)} on {ctx.env.name}, not the same"
        elif not want and have:
            f["cues-extra"] = f"{len(have)} cues on {ctx.env.name}, the note has no timed rows"
    if not f:
        f["ok"] = ""
    return st


def assess_all(paths, ctx: Ctx, *, artist_op=None, progress=True):
    states = []
    notes = la.collect(paths)
    for i, p in enumerate(notes, 1):
        try:
            n = an.parse_note(p)
        except Exception as exc:  # noqa: BLE001
            print(f"cannot parse {p}: {exc}", file=sys.stderr)
            continue
        if artist_op and not any(c.op == artist_op for c in n.contributors):
            continue
        if progress:
            print(f"\r  reading {i}/{len(notes)} on {ctx.env.name} …", end="", file=sys.stderr, flush=True)
        states.append(assess(n, ctx))
    if progress:
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    _mark_unrecorded_twins(states)
    return states


def _mark_unrecorded_twins(states):
    """Twins (same text and artist, so the same ledger key) that no ledger entry tells
    apart yet — e.g. both new on a fresh environment. Only one may be uploaded, or the
    text gets the same recitation twice: keep the one another environment's ledger
    uploaded, else the first that is not blocked (by path), and mark the others twin."""
    elsewhere = {}                                  # key -> note uploaded on another environment
    for path in sorted(LEDGERS.glob("*/audio.json")):
        try:
            for k, e in json.loads(path.read_text(encoding="utf-8")).items():
                if e.get("audio_id") and e.get("note"):
                    elsewhere.setdefault(k, note_key(e["note"]))
        except (OSError, ValueError):
            continue
    groups = {}
    for st in states:
        if "twin" not in st.flags and not (st.entry and st.entry.get("audio_id")):
            groups.setdefault(st.key, []).append(st)
    for key, group in groups.items():
        if len(group) < 2:
            continue
        keep = sorted(group, key=lambda s: (note_key(s.rel) != elsewhere.get(key), "blocked" in s.flags,
                                            str(s.rel)))[0]
        for st in group:
            if st is not keep:
                st.flags.clear()
                st.flags["twin"] = f"same text and artist as {keep.rel}, which is the one to upload"


# ------------------------------------------------------------------ steps

def _save_entry(st: State, ctx: Ctx, **kw):
    e = ctx.ledger.entry(st.key)
    e.update({"note": str(st.rel), "text_id": st.res["text_id"], "edition_id": st.res["edition_id"]})
    e.update(kw)
    st.entry = e
    ctx.ledger.save()
    return e


def _create_audio(st, ctx):
    out = ctx.client.post(f"/v2/texts/{st.res['text_id']}/audios", st.res["audio_input"])
    _save_entry(st, ctx, audio_id=out["id"])
    return f"audio {out['id']}"


def _create_recording(st, ctx):
    rec, e = st.rec, st.entry
    url = f"/v2/audios/{e['audio_id']}/recordings"
    before = {x["id"] if isinstance(x, dict) else x for x in items(ctx.client.get(url))}
    try:
        out = ctx.client.post_file(url, rec["metadata"], Path(rec["file"]), rec["content_type"])
    except ApiError as exc:
        if exc.status is not None:
            raise
        time.sleep(10)          # connection dropped: was it stored anyway?
        now = {x["id"] if isinstance(x, dict) else x for x in items(ctx.client.get(url))}
        if now - before:
            raise ApiError(f"{exc}; the audio now has recording(s) {sorted(now - before)}: check by hand") from None
        out = ctx.client.post_file(url, rec["metadata"], Path(rec["file"]), rec["content_type"])
    back = ctx.client.get(f"/v2/recordings/{out['id']}")
    _save_entry(st, ctx, recording_id=out["id"], file=rec["file"], sha256=rec["sha256"],
                recording={k: back.get(k) for k in ("format", "size_bytes", "duration_ms", "edition_id")})
    st.entry.pop("cues", None)
    ctx.ledger.save()
    ok = back.get("size_bytes") == rec["size_bytes"]
    return f"recording {out['id']}, {back.get('size_bytes')} bytes" + ("" if ok else "  SIZE MISMATCH")


def _put_cues(st, ctx):
    rid = st.entry["recording_id"]
    ctx.client.put(f"/v2/recordings/{rid}/cues", st.res["cues"])
    back = items(ctx.client.get(f"/v2/recordings/{rid}/cues"))
    if _cue_tuples(back) != _cue_tuples(st.res["cues"]):
        raise ApiError(f"cues read back from {rid} differ from what was sent")
    _save_entry(st, ctx, cues={"count": len(back), "uploaded": time.strftime("%Y-%m-%d")})
    return f"{len(back)} cues read back"


def _patch_audio(st, ctx, fields):
    ctx.client.patch(f"/v2/audios/{st.entry['audio_id']}", fields)
    return "updated: " + ", ".join(fields)


def _patch_recording(st, ctx, fields):
    ctx.client.patch(f"/v2/recordings/{st.entry['recording_id']}", fields)
    return "updated: " + ", ".join(fields)


def _follow_edition(st, ctx):
    rid = st.entry["recording_id"]
    ctx.client.put(f"/v2/recordings/{rid}/edition/{st.res['edition_id']}")
    st.entry.setdefault("recording", {})["edition_id"] = st.res["edition_id"]
    st.entry.pop("cues", None)              # the server drops cues when the edition changes
    _save_entry(st, ctx)
    return f"follows {st.res['edition_id']} (its cues were removed by the server)"


def _replace_recording(st, ctx):
    old = {k: st.entry.get(k) for k in ("recording_id", "file", "sha256", "recording", "cues")}
    got = _create_recording(st, ctx)
    ctx.client.delete(f"/v2/recordings/{old['recording_id']}")
    old["deleted"] = time.strftime("%Y-%m-%d")
    st.entry.setdefault("replaced", []).append({k: v for k, v in old.items() if v is not None})
    ctx.ledger.save()
    return f"{got}; old recording {old['recording_id']} deleted (its file stays in the server's storage)"


def _history(st, ctx, what, **ids):
    st.entry.setdefault("deleted", []).append(dict(ids, what=what, date=time.strftime("%Y-%m-%d")))
    ctx.ledger.save()


def _delete_cues(st, ctx):
    ctx.client.delete(f"/v2/recordings/{st.entry['recording_id']}/cues")
    st.entry.pop("cues", None)
    _history(st, ctx, "cues", recording_id=st.entry["recording_id"])
    return "cues deleted"


def _delete_recording(st, ctx):
    rid = st.entry.pop("recording_id")
    ctx.client.delete(f"/v2/recordings/{rid}")
    for k in ("recording", "cues", "sha256", "file"):
        st.entry.pop(k, None)
    _history(st, ctx, "recording", recording_id=rid)
    return f"recording {rid} deleted (its file stays in the server's storage)"


def _delete_audio(st, ctx):
    aid = st.entry.pop("audio_id")
    ctx.client.delete(f"/v2/audios/{aid}")
    _history(st, ctx, "audio", audio_id=aid)
    return f"audio {aid} deleted"


# ------------------------------------------------------------------ plans

def _plan(st) -> NotePlan:
    return NotePlan(title=st.title, link=st.link)


def _blocked(st) -> str | None:
    for flag in NO_WRITE:
        if flag in st.flags:
            return f"{flag}: {st.flags[flag]}"
    return None


def plan_push(states, ctx, only):
    plans = []
    for st in states:
        f = st.flags
        p = _plan(st)
        wants_audio = "new" in f and "audio" in only
        wants_rec = ("new" in f or "partial" in f) and "recording" in only
        wants_cues = "cues" in only and bool(st.res.get("cues")) and (
            "cues-pending" in f or "cues-changed" in f or (("new" in f or "partial" in f) and wants_rec))
        if not (wants_audio or wants_rec or wants_cues):
            continue
        p.problem = _blocked(st)
        if not p.problem and "new" in f and wants_rec and not wants_audio:
            p.problem = "no audio on this environment yet: include `audio` in --only"
        if not p.problem and wants_rec and (not st.rec or not st.rec.get("file")):
            p.problem = "the recording's file is not found locally (download it, or pass --audio-dir)"
        if not p.problem and "file-changed" in f:
            p.problem = "the local file is not the uploaded one: use `replace`"
        if not p.problem:
            n_cues = len(st.res.get("cues") or [])
            if wants_audio:
                p.steps.append(Step(f"POST /v2/texts/{st.res['text_id']}/audios",
                                    lambda st=st: _create_audio(st, ctx)))
            if wants_rec:
                r = st.rec
                p.steps.append(Step(f"POST /v2/audios/<audio>/recordings  ({Path(r['file']).name}, "
                                    f"{r['size_bytes'] / 1048576:.1f} MB)", lambda st=st: _create_recording(st, ctx)))
            if wants_cues:
                p.steps.append(Step(f"PUT /v2/recordings/<recording>/cues  ({n_cues} cues)",
                                    lambda st=st: _put_cues(st, ctx)))
        plans.append(p)
    return plans


def plan_update(states, ctx, only):
    plans = []
    for st in states:
        f = st.flags
        if not any(k in f for k in ("audio-details", "recording-details", "recording-edition")):
            continue
        p = _plan(st)
        p.problem = _blocked(st)
        if not p.problem:
            if "audio" in only and "audio-details" in f:
                fields = _audio_diff(st.live_audio, st.res["audio_input"])
                p.steps.append(Step(f"PATCH /v2/audios/{st.entry['audio_id']}  ({', '.join(fields)})",
                                    lambda st=st, fields=fields: _patch_audio(st, ctx, fields)))
            if "recording" in only and "recording-details" in f:
                fields = _rec_diff(st.live_rec, st.rec["metadata"])
                p.steps.append(Step(f"PATCH /v2/recordings/{st.entry['recording_id']}  ({', '.join(fields)})",
                                    lambda st=st, fields=fields: _patch_recording(st, ctx, fields)))
            if "recording" in only and "recording-edition" in f:
                p.steps.append(Step(f"PUT /v2/recordings/{st.entry['recording_id']}/edition/{st.res['edition_id']}",
                                    lambda st=st: _follow_edition(st, ctx)))
                if st.res.get("cues"):
                    p.steps.append(Step(f"PUT /v2/recordings/{st.entry['recording_id']}/cues  "
                                        f"({len(st.res['cues'])} cues, again: changing edition drops them)",
                                        lambda st=st: _put_cues(st, ctx)))
        if p.steps or p.problem:
            plans.append(p)
    return plans


def plan_replace(states, ctx):
    plans = []
    for st in states:
        if "file-changed" not in st.flags:
            continue
        p = _plan(st)
        p.problem = _blocked(st)
        if not p.problem and not (st.rec and st.rec.get("file")):
            p.problem = "the new file is not found locally"
        if not p.problem:
            r = st.rec
            p.steps.append(Step(f"POST /v2/audios/{st.entry['audio_id']}/recordings  ({Path(r['file']).name}, "
                                f"{r['size_bytes'] / 1048576:.1f} MB), then DELETE /v2/recordings/"
                                f"{st.entry['recording_id']}", lambda st=st: _replace_recording(st, ctx)))
            if st.res.get("cues"):
                p.steps.append(Step(f"PUT /v2/recordings/<new>/cues  ({len(st.res['cues'])} cues)",
                                    lambda st=st: _put_cues(st, ctx)))
        plans.append(p)
    return plans


def plan_delete(states, ctx, only):
    plans = []
    for st in states:
        e = st.entry or {}
        p = _plan(st)
        if "twin" in st.flags:
            p.problem = st.flags["twin"]
        elif not e.get("audio_id"):
            p.problem = f"nothing from this note on {ctx.env.name}"
        else:
            rid = e.get("recording_id")
            if "cues" in only and rid and st.live_cues and "recording" not in only:
                p.steps.append(Step(f"DELETE /v2/recordings/{rid}/cues  ({len(st.live_cues)} cues)",
                                    lambda st=st: _delete_cues(st, ctx)))
            if "recording" in only and rid:
                p.steps.append(Step(f"DELETE /v2/recordings/{rid}  (with its cues)",
                                    lambda st=st: _delete_recording(st, ctx)))
            if "audio" in only:
                others = [r for r in (st.live_audio or {}).get("recordings", []) if r != rid or "recording" not in only]
                if others:
                    p.problem = (f"the audio still has recording(s) {others}: the API deletes only an audio "
                                 "without recordings (add `recording` to --only)")
                    p.steps = []
                else:
                    p.steps.append(Step(f"DELETE /v2/audios/{e['audio_id']}", lambda st=st: _delete_audio(st, ctx)))
        if p.steps or p.problem:
            plans.append(p)
    return plans


# ------------------------------------------------------------------ verify

def verify(states, ctx) -> list:
    """Deep read-only checks on what is uploaded. -> [(state, [problems])]"""
    out = []
    contents = {}
    for st in states:
        e, live_r = st.entry or {}, st.live_rec
        if not e.get("recording_id") or live_r is None:
            continue
        probs = [f"{k}: {v}" for k, v in st.flags.items() if k not in ("ok", "blocked")]
        rec = st.rec
        f = e.get("file")
        if f and os.path.isfile(f) and live_r.get("size_bytes") != os.path.getsize(f):
            probs.append(f"size on {ctx.env.name} {live_r.get('size_bytes')} != local {os.path.getsize(f)}")
        if rec and rec.get("size_bytes") and live_r.get("size_bytes") != rec["size_bytes"] and not f:
            probs.append(f"size on {ctx.env.name} {live_r.get('size_bytes')} != note's file {rec['size_bytes']}")
        if not live_r.get("duration_ms"):
            probs.append("no duration")
        cues = sorted(st.live_cues or [], key=lambda c: c["start_ms"])
        if cues:
            ed = live_r.get("edition_id")
            if ed not in contents:
                contents[ed] = ctx.client.get(f"/v2/editions/{ed}/content")
            length = len(contents[ed])
            for a, b in zip(cues, cues[1:]):
                if b["start_ms"] < a["end_ms"]:
                    probs.append(f"cues overlap in time at {b['start_ms']} ms")
                if b["span"]["start"] < a["span"]["end"]:
                    probs.append(f"cues overlap in text at char {b['span']['start']}")
            for c in cues:
                if not 0 <= c["start_ms"] < c["end_ms"] <= (live_r.get("duration_ms") or 0) + la.DURATION_SLACK_MS:
                    probs.append(f"cue {c['start_ms']}-{c['end_ms']} ms outside the recording")
                if not 0 <= c["span"]["start"] < c["span"]["end"] <= length:
                    probs.append(f"cue span {c['span']} outside the edition ({length} chars)")
        for old in e.get("replaced", []) + [d for d in e.get("deleted", []) if d.get("what") == "recording"]:
            if ctx.client.get_or_none(f"/v2/recordings/{old['recording_id']}") is not None:
                probs.append(f"recording {old['recording_id']} should be deleted but still exists")
        out.append((st, sorted(set(probs))))
    return out
