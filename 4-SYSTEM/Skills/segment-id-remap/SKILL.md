---
name: segment-id-remap
description: >
  Send segment id maps (old_segment_id -> new_segment_id) to the WeBuddhist studio's id-remap
  endpoint, so its own records that point at segments (sub-tasks, tag segments, verse bookmarks,
  recitation play times, live-edition settings) move from the old segment ids to the new ones.
  Use after a text has been re-segmented on the library and its segment id map exists, whenever
  asked to "remap the segment ids", "hit the id-remap endpoint", "send the mapping to studio", or
  to update bookmarks / plans after a re-segmentation. The person gives the map path(s) in the
  prompt; there is no default path.
---

# segment-id-remap

After a re-segmentation (library-upload `replace`, see the `segment-mapping` skill) every
segment has a new id. The library's own alignments are already moved by `replace`; the
**studio** keeps other records keyed by segment id, and this skill tells it to move them.

## The map

The person names where the map(s) are, in the prompt. Never assume a path; ask if none is
given. A map is one JSON file per text:

```json
{"segment": [{"old_segment_id": "3WtlOM…", "new_segment_id": "qnH5xD…"},
             {"old_segment_id": "7IQ11o…", "new_segment_id": "deleted"}]}
```

(`library.py text replace --map` writes them as `4-SYSTEM/reports/<env>/segment-id-maps/<note>.json`;
the `<note>.detail.json` beside each is for people and is never sent.) Use the maps of the
**environment the studio reads** — prod's — unless the person says otherwise.

## Sending

```
python 4-SYSTEM/Skills/segment-id-remap/scripts/id_remap.py <map files or folders>            # dry run: lists what would go
python 4-SYSTEM/Skills/segment-id-remap/scripts/id_remap.py <map files or folders> --yes --log <file>
```

- One `POST https://studio.webuddhist.com/api/v1/cms/admin/id-remap/segments` per map, one at
  a time, the map's JSON as the body. `--url` overrides the endpoint.
- The bearer token is read from `.env` (vault root or `4-SYSTEM/scripts/`) as `bearer_token`.
  Never print it, never ask for it in chat; if it is missing, tell the person to add it.
- A failed request (non-2xx) stops the run there: the rest would most likely fail the same way.

## Reading the response

```json
{"requested": 20,
 "updated_counts": {"sub_tasks.segment_ids": 0, "tag_segments": 0, "bookmarks(VERSE)": 0,
                    "recitation_segment_play_times.segment_id": 0, "live_edition_settings.segments": 0},
 "skipped": []}
```

- Every count 0 and `skipped` empty: nothing in the studio pointed at those segments. Pay no
  mind; the script prints one quiet line.
- **Any count above 0, or anything in `skipped`: tell the person** — which text, which
  counts, what was skipped. The script prints these in full and lists them again at the end.

## Rules

- Do one map first; only when its response looks right, send the rest.
- Send only maps whose re-segmentation is finished (the new ids are live). Sending a map
  twice is harmless (the old ids are gone, so nothing matches again), but do not resend
  without a reason.
- Keep the `--log` file of every run; report its path.
