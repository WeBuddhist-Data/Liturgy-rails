"""Ledgers: what this vault has created on each environment, with the ids the server returned.

One JSON file per environment and kind: 4-SYSTEM/ledgers/<env>/<kind>.json (git-ignored;
outside the skill folder, so re-installing the skill keeps them). Keys are vault-relative
note paths. An id is written the moment the server returns it, so an interrupted run
resumes instead of creating a second copy. Nothing is silently dropped: a delete moves
ids into the entry's `deleted` history.

The notes' frontmatter records the ids of one environment (vault.FRONTMATTER_ENV); the
ledgers record every environment's, including that one.
"""
from __future__ import annotations

import json
import os

from .vault import LEDGERS


class Ledger:
    def __init__(self, env: str, kind: str):
        self.env, self.kind = env, kind
        self.path = LEDGERS / env / f"{kind}.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def entry(self, key) -> dict:
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
