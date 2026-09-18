"""Fetch a FIBA game page and pull the JSON out of it.

Fetch, parse, return a dict. Nothing else — no cleaning, no renaming, no
maths, no database. All of that is dbt's job downstream. Keep it that way:
the whole point of the raw layer is that a wrong formula costs a re-run
instead of a re-scrape.

The pages are Next.js App Router, fully server-rendered, so a plain GET gets
you everything including play-by-play with shot coordinates. No Selenium.
It arrives as RSC wire format — self.__next_f.push([1, "<id>:<json>"]) script
tags, chunk ids varying in length page to page.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import requests
from bs4 import BeautifulSoup

BASE = "https://www.fiba.basketball"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Next.js writes missing values as this string. Not null. Bites every time.
UNDEFINED = "$undefined"

# A node with all three of these is the game. Nothing else on the page has them.
GAME_NODE_KEYS = frozenset({"game", "playersTeamA", "gameDetails"})

_PREFIX = "self.__next_f.push("
_CHUNK_ID = re.compile(r"^[0-9a-fA-F]+:")
_TYPE_TAG = re.compile(r"^[A-Za-z]?(?=[\[{])")
_GAME_ID_IN_URL = re.compile(r"/games/(\d+)")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


class GameNotFound(Exception):
    """The page loaded but carried no game payload."""


@dataclass
class RawGame:
    """One game as FIBA served it, plus enough to know where it came from."""

    game_id: str
    source_url: str
    fetched_at: datetime
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def periods(self) -> dict[str, dict[str, Any]]:
        """Q1..Q4, plus OT1.. if it went long.

        Nested two deep: playByPlay.items is keyed by period, and each period
        has its own items list.
        """
        pbp = self.payload.get("playByPlay") or {}
        items = pbp.get("items") or {}
        return items if isinstance(items, dict) else {}

    def action_count(self) -> int:
        return sum(len(p.get("items") or []) for p in self.periods().values())

    def period_scores(self) -> dict[str, tuple[Any, Any]]:
        """Running total at the end of each period. NOT points scored in it.

        Caught me out: Canada's Q1-Q4 in game 135067 read 22, 40, 59, 72 with
        a 72-point final. Want the box-score numbers? Use period_points().
        """
        return {
            name: (block.get("scoreA"), block.get("scoreB"))
            for name, block in self.periods().items()
        }

    def period_points(self) -> dict[str, tuple[int, int]]:
        """Points scored IN each period, differenced from the running totals."""
        points: dict[str, tuple[int, int]] = {}
        prev_a = prev_b = 0
        for name, (cum_a, cum_b) in self.period_scores().items():
            cur_a, cur_b = int(cum_a or 0), int(cum_b or 0)
            points[name] = (cur_a - prev_a, cur_b - prev_b)
            prev_a, prev_b = cur_a, cur_b
        return points

    def final_score(self) -> tuple[Any, Any]:
        """Final score, straight off the top-level fields."""
        return (
            undefined_to_none(self.payload.get("teamAScore")),
            undefined_to_none(self.payload.get("teamBScore")),
        )

    def summary(self) -> dict[str, Any]:
        """For logs and smoke tests. Don't build anything on this.

        dbt reads the payload column directly. This just keeps a failed ingest
        readable in the Airflow log without dumping a megabyte of JSON.
        """
        game = self.payload.get("game") or {}
        team_a = game.get("teamA") or {}
        team_b = game.get("teamB") or {}
        return {
            "game_id": self.game_id,
            "date": (undefined_to_none(game.get("gameDateTime")) or "")[:10],
            "home": team_a.get("code"),
            "away": team_b.get("code"),
            "home_score": undefined_to_none(self.payload.get("teamAScore")),
            "away_score": undefined_to_none(self.payload.get("teamBScore")),
            "competition": (undefined_to_none(game.get("competition")) or {}).get(
                "officialName"
            ),
            "players": len(self.payload.get("playersTeamA") or [])
            + len(self.payload.get("playersTeamB") or []),
            "periods": list(self.periods().keys()),
            "period_points": self.period_points(),
            "pbp_actions": self.action_count(),
        }


def undefined_to_none(value: Any) -> Any:
    return None if value == UNDEFINED else value


def fetch(url: str, tries: int = 5, pause: float = 3.0) -> str:
    """GET with retries. FIBA drops connections when a tournament is live.

    Linear backoff, not exponential — these are short connection resets, not
    rate limiting, so there's nothing to be gained by waiting minutes.
    """
    last: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            response = SESSION.get(url, timeout=60)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 - retry anything transport-level
            last = exc
            if attempt < tries:
                time.sleep(pause * attempt)
    raise RuntimeError(f"Failed to fetch after {tries} attempts: {url}") from last


def rsc_payloads(html: str) -> Iterator[Any]:
    """Every JSON payload in the page's script tags.

    Biggest first — the game data is always the largest thing on the page, so
    callers usually hit it on the first yield and stop.
    """
    soup = BeautifulSoup(html, "html.parser")
    scripts = sorted(
        (s.get_text() for s in soup.find_all("script")), key=len, reverse=True
    )
    for text in scripts:
        if not text.startswith(_PREFIX):
            continue
        try:
            outer = json.loads(text[len(_PREFIX) : -1])
        except ValueError:
            continue
        if not (isinstance(outer, list) and len(outer) > 1 and isinstance(outer[1], str)):
            continue
        body = _TYPE_TAG.sub("", _CHUNK_ID.sub("", outer[1]))
        try:
            yield json.loads(body)
        except ValueError:
            # Some chunks are split mid-string across pushes. Fine to skip —
            # the game node always arrives whole in one chunk.
            continue


def find_nodes(obj: Any, required: frozenset[str], limit: int | None = None) -> list[dict]:
    """Find dicts anywhere in the tree that have all of `required` as keys."""
    found: list[dict] = []

    def walk(node: Any, depth: int) -> None:
        if depth > 30 or (limit is not None and len(found) >= limit):
            return
        if isinstance(node, dict):
            if required <= node.keys():
                found.append(node)
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(obj, 0)
    return found


def game_id_from_url(url: str) -> str | None:
    match = _GAME_ID_IN_URL.search(url)
    return match.group(1) if match else None


def parse_game(html: str, source_url: str) -> RawGame:
    """Pull one game's payload out of a fetched page.

    Raises GameNotFound for scheduled-but-not-played fixtures, which have a
    page but no game node.
    """
    for payload in rsc_payloads(html):
        hits = find_nodes(payload, GAME_NODE_KEYS, limit=1)
        if not hits:
            continue

        node = hits[0]
        game = node.get("game") or {}
        game_id = str(
            game.get("gameId") or game_id_from_url(source_url) or ""
        ).strip()
        if not game_id:
            raise GameNotFound(f"Game node found but carries no gameId: {source_url}")

        # Warn, don't fail. Some finished games genuinely have no play-by-play
        # (FIBA backfills certain events) and we'd rather have the box score
        # than nothing.
        warnings = [
            f"missing {key}"
            for key in ("playByPlay", "playersTeamA", "playersTeamB")
            if not node.get(key)
        ]

        return RawGame(
            game_id=game_id,
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc),
            payload=node,
            warnings=warnings,
        )

    raise GameNotFound(f"No game payload on page: {source_url}")


def scrape_game(url: str) -> RawGame:
    return parse_game(fetch(url), url)


if __name__ == "__main__":  # pragma: no cover - manual spot check
    import sys

    for arg in sys.argv[1:]:
        raw = scrape_game(arg)
        print(json.dumps(raw.summary(), indent=2))
        if raw.warnings:
            print("  warnings:", ", ".join(raw.warnings))
