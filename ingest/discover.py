"""Turn an event slug into game URLs.

The DAG takes a slug, not a list of URLs. Tournaments add games as rounds
resolve, so any hardcoded list is stale as soon as the bracket fills in.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ingest.fiba import BASE, fetch, find_nodes, rsc_payloads, undefined_to_none

log = logging.getLogger(__name__)

EVENTS_URL = f"{BASE}/en/events"
CONFIG_PATH = Path(os.environ.get("EVENTS_CONFIG", "config/events.yml"))
SCHEDULE_KEYS = frozenset({"gameId", "teamA", "teamB"})
EVENT_KEYS = frozenset({"slug", "fibaOfficialName"})


def list_events() -> list[dict]:
    """Everything FIBA currently publishes on its events index.

    Comes back as roughly 200 events across every discipline and age group,
    so it's raw material for filtering, not something to iterate over blindly.
    """
    html = fetch(EVENTS_URL)
    for payload in rsc_payloads(html):
        nodes = find_nodes(payload, EVENT_KEYS)
        if not nodes:
            continue

        events: dict[str, dict] = {}
        for node in nodes:
            slug = undefined_to_none(node.get("slug"))
            name = undefined_to_none(node.get("fibaOfficialName")) or undefined_to_none(
                node.get("title")
            )
            if not slug or not name or slug in events:
                continue

            # Host is buried a couple of levels down and often absent.
            host = ""
            hosts = undefined_to_none(node.get("fibaHostJson")) or []
            if isinstance(hosts, list) and hosts:
                cities = hosts[0].get("cities") or []
                city = cities[0].get("name", "") if cities else ""
                country = hosts[0].get("countryName", "") or ""
                host = ", ".join(part for part in (city, country) if part)

            events[slug] = {
                "slug": slug,
                "name": name,
                "start": (undefined_to_none(node.get("eventDateStart")) or "")[:10],
                "end": (undefined_to_none(node.get("eventDateEnd")) or "")[:10],
                "host": host,
                "discipline": undefined_to_none(node.get("fibaSource")) or "",
            }

        if events:
            return sorted(events.values(), key=lambda e: e["start"])

    raise RuntimeError("Couldn't read the event index — page structure may have changed.")


def event_games(slug: str, played_only: bool = True) -> list[dict]:
    """Every fixture in an event, with the URL for each game page.

    Don't be tempted to use the score to decide if a game is done — a live
    game has a score too, and you'd freeze a half-finished box score into the
    warehouse. gameStatisticStatusCode flips EMPTY -> VALID when the stats are
    final. That's the one to trust.
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
            # No team code = the bracket hasn't resolved yet, not missing data.
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


def event_teams(slug: str) -> set[str]:
    """Team codes appearing anywhere in an event's schedule, played or not."""
    codes: set[str] = set()
    for game in event_games(slug, played_only=False):
        for code in (game["home"], game["away"]):
            if code:
                codes.add(code.upper())
    return codes


def events_for_team(
    team_code: str = "CAN",
    since: str | None = None,
    until: str | None = None,
    disciplines: tuple[str, ...] = ("gdap",),
) -> list[dict]:
    """Events the team appears in, within a date window.

    One extra fetch per candidate event, so narrow it down before calling.
    The index carries no team list — the only way to know Canada is playing is
    to read the schedule.

    `disciplines` filters on FIBA's own source tag; 'gdap' is 5x5. Pass an
    empty tuple to include 3x3 and everything else.
    """
    team_code = team_code.upper()
    candidates = []
    for event in list_events():
        if disciplines and event["discipline"] not in disciplines:
            continue
        # An event with no end date hasn't been scheduled properly; skip it
        # rather than fetch a page that won't have a game list.
        if since and (event["end"] or event["start"]) < since:
            continue
        if until and event["start"] and event["start"] > until:
            continue
        candidates.append(event)

    log.info("checking %d candidate event(s) for %s", len(candidates), team_code)

    found = []
    for event in candidates:
        try:
            teams = event_teams(event["slug"])
        except Exception as exc:  # noqa: BLE001 - one bad event shouldn't stop the sweep
            log.warning("skipping %s — %s", event["slug"], exc)
            continue
        if team_code in teams:
            found.append(event)
            log.info("  %s  %s", event["start"], event["name"])

    return found


if __name__ == "__main__":  # pragma: no cover - manual run
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for game in event_games(sys.argv[1]):
        print(
            f"{game['game_id']}  {game['date']}  "
            f"{game['home']}-{game['away']:<4} {game['round'][:28]:<28} {game['url']}"
        )


def load_config(path: Path | None = None) -> dict:
    import yaml

    path = path or CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"No events config at {path}")
    return yaml.safe_load(path.read_text()) or {}


def select_events(config: dict | None = None) -> list[str]:
    """The event slugs this run should ingest: pinned, plus anything discovered.

    Pinned entries are never verified against the index — that is the point of
    them. A finished tournament disappears from FIBA's index while its pages
    stay up, so requiring it to be discoverable would quietly drop it.
    """
    cfg = config or load_config()
    slugs = list(dict.fromkeys(cfg.get("pinned") or []))
    log.info("%d pinned event(s)", len(slugs))

    discover = cfg.get("discover") or {}
    if discover.get("enabled"):
        found = events_for_team(
            team_code=cfg.get("team", "CAN"),
            since=discover.get("since"),
            until=discover.get("until"),
            disciplines=tuple(discover.get("disciplines") or ()),
        )
        new = [e["slug"] for e in found if e["slug"] not in slugs]
        log.info("discovery added %d event(s): %s", len(new), ", ".join(new) or "none")
        slugs.extend(new)

    return slugs
