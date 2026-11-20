"""Ledgers: what this vault has created on each environment, with the ids the server returned.

One JSON file per environment and kind: 4-SYSTEM/ledgers/<env>/<kind>.json
(e.g. ledgers/dev/audio.json). An id is written the moment the server returns it,
so an interrupted run resumes instead of creating a second copy. Entries are never
silently dropped: a delete moves the ids into the entry's `deleted` history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .envs import VAULT

ROOT = VAULT / "4-SYSTEM" / "ledgers"


class Ledger:
    def __init__(self, env: str, kind: str):
        self.env, self.kind = env, kind
        self.path = ROOT / env / f"{kind}.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def entry(self, key) -> dict:
        """The entry for `key`, created (empty) if missing."""
        return self.data.setdefault(key, {})

    def items(self):
        return self.data.items()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def __len__(self):
        return len(self.data)
