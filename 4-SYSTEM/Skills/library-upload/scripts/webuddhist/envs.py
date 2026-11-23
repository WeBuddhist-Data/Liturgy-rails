"""The library's environments: dev, testing and prod are built in; a vault can add or
override one in 4-SYSTEM/library-upload.json under "environments".

  dev      https://dev.library.webuddhist.com/api   key: dev_api_key
  testing  https://testing.library.webuddhist.com/api   key: testing_api_key
  prod     https://library-prod.webuddhist.com      key: prod_api_key   (protected)

Each runs its own build of the library API: dev the backend's `dev` branch, testing its
`main` branch (everything merged from dev), prod only a release tagged vX.Y.Z on main.
So an endpoint can be on dev and testing and not yet on prod; `GET <url>/__/health`
shows the build. They are separate databases: testing is a copy of prod refreshed each
release cycle, dev is reset from prod by hand. Texts from before the last copy share
prod's ids; texts created since exist only where they were created, and a refresh wipes
what was pushed only to testing. Ids are therefore always checked per environment, never
assumed to carry over.

A `protected` environment needs `--confirm <name>` on top of `--yes` for any write.
The API key (sent as X-API-Key) is read, in order, from the environment variables
WEBUDDHIST_API_KEY_<ENV>, <ENV>_API_KEY, `key_var`, then from the same names and
`key_dotenv` (e.g. `dev_api_key=…`, `testing_api_key=…`, `prod_api_key=…`) in the git-ignored `.env`
at the vault root or in 4-SYSTEM/scripts/. Each environment reads only its own names,
so a prod key is never sent to dev or testing. It is never printed. Every environment
needs its key, for reads too (dev since 2026-11-23).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .vault import CONFIG, VAULT


@dataclass(frozen=True)
class Env:
    name: str
    url: str
    protected: bool = False
    key_var: str | None = None
    key_dotenv: tuple = field(default_factory=tuple)

    def _names(self):
        return [f"WEBUDDHIST_API_KEY_{self.name.upper()}", f"{self.name.upper()}_API_KEY",
                *([self.key_var] if self.key_var else []), *self.key_dotenv]

    def api_key(self) -> str | None:
        for n in self._names():
            if os.environ.get(n):
                return os.environ[n]
        for dotenv in (VAULT / ".env", VAULT / "4-SYSTEM" / "scripts" / ".env"):
            if not dotenv.is_file():
                continue
            values = {}
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().removeprefix("export ").partition("=")
                values[k.strip()] = v.strip().strip("\"'")
            for n in self._names():
                if values.get(n):
                    return values[n]
        return None

    def headers(self) -> dict:
        h = {}
        key = self.api_key()
        if key:
            h["X-API-Key"] = key
        if os.environ.get("WEBUDDHIST_APP"):
            h["X-Application"] = os.environ["WEBUDDHIST_APP"]
        return h


ENVIRONMENTS = {
    "dev": Env("dev", "https://dev.library.webuddhist.com/api", key_dotenv=("dev_api_key",)),
    "testing": Env("testing", "https://testing.library.webuddhist.com/api", key_dotenv=("testing_api_key",)),
    "prod": Env("prod", "https://library-prod.webuddhist.com", protected=True,
                key_dotenv=("prod_api_key", "WEBUDDHIST_API_KEY", "api_key")),   # the last two: older .env names
}
for _name, _e in CONFIG.get("environments", {}).items():
    ENVIRONMENTS[_name] = Env(_name, _e["url"], protected=bool(_e.get("protected", False)),
                              key_var=_e.get("key_var"), key_dotenv=tuple(_e.get("key_dotenv", ())))


def get(name: str) -> Env:
    try:
        return ENVIRONMENTS[name]
    except KeyError:
        raise SystemExit(f"unknown environment {name!r}; known: {', '.join(ENVIRONMENTS)}") from None
