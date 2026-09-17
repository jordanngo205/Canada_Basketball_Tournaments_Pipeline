"""Find the game URLs for an event, so nobody has to paste one by hand.

The scheduled DAG runs against an event slug, not a list of URLs — a
tournament adds games as rounds resolve, and a hardcoded list goes stale the
moment the bracket fills in.
"""

from __future__ import annotations

import logging

from ingest.fiba import BASE, fetch, find_nodes, rsc_payloads, undefined_to_none

log = logging.getLogger(__name__)

SCHEDULE_KEYS = frozenset({"gameId", "teamA", "teamB"})


def event_games(slug: str, played_only: bool = True) -> list[dict]:
    """Every fixture in an event, each with the URL its game page lives at.

    `played_only` drops anything without a usable box score. That check is not
    "does it have a score" — a live game shows a running score, and ingesting
    one would freeze a half-finished box into the warehouse. FIBA flips
    `gameStatisticStatusCode` from EMPTY to VALID when the stats are final,
    which is the signal that actually means finished.
    """
    url = f"{BASE}/en/events/{slug}/games"
    html = fetch(url)

    nodes: list[dict] = []
    for payload in rsc_payloads(html):
        found = find_nodes(payload, SCHEDULE_KEYS)
        if found:
            nodes = found
            break
    if not nodes:
        raise RuntimeError(f"Could not find the games list on {url}")

    games: dict[str, dict] = {}
    for node in nodes:
        game_id = undefined_to_none(node.get("gameId"))
        if game_id is None or str(game_id) in games:
            continue
        team_a = undefined_to_none(node.get("teamA")) or {}
        team_b = undefined_to_none(node.get("teamB")) or {}
        home = undefined_to_none(team_a.get("code"))
        away = undefined_to_none(team_b.get("code"))
        status = undefined_to_none(node.get("gameStatisticStatusCode"))
        is_live = bool(undefined_to_none(node.get("isLive")))

        games[str(game_id)] = {
            "game_id": str(game_id),
            "home": home,
            "away": away,
            "date": (undefined_to_none(node.get("gameDateTime")) or "")[:10],
            "round": (undefined_to_none(node.get("round")) or {}).get("roundName", ""),
            "status": status,
            "is_live": is_live,
            # A knockout slot has no team code until the bracket resolves, so a
            # missing code means "not a real fixture yet", not "missing data".
            "played": bool(home and away and status == "VALID" and not is_live),
            "url": f"{BASE}/en/events/{slug}/games/{game_id}-{home}-{away}",
        }

    rows = sorted(games.values(), key=lambda r: (r["date"], r["game_id"]))
    if played_only:
        rows = [r for r in rows if r["played"]]
    log.info("event %s: %d game(s) ready to ingest", slug, len(rows))
    return rows


def game_urls(slug: str, played_only: bool = True) -> list[str]:
    return [g["url"] for g in event_games(slug, played_only)]


if __name__ == "__main__":  # pragma: no cover - manual run
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for game in event_games(sys.argv[1]):
        print(
            f"{game['game_id']}  {game['date']}  "
            f"{game['home']}-{game['away']:<4} {game['round'][:28]:<28} {game['url']}"
        )
