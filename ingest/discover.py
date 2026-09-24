"""Turn an event slug into game URLs.

The DAG takes a slug, not a list of URLs. Tournaments add games as rounds
resolve, so any hardcoded list is stale as soon as the bracket fills in.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ingest.fiba import BASE, fetch, find_nodes, rsc_payloads, undefined_to_none

log = logging.getLogger(__name__)

EVENTS_URL = f"{BASE}/en/events"
CONFIG_PATH = Path(os.environ.get("EVENTS_CONFIG", "config/events.yml"))
SCHEDULE_KEYS = frozenset({"gameId", "teamA", "teamB"})
EVENT_KEYS = frozenset({"slug", "fibaOfficialName"})

# FIBA's event index carries `gender`, `fibaGender` and `genderFilter` fields
# and leaves all three as `$undefined` on every one of the 139 events it
# publishes. They are not optional-but-usually-there; they are never populated.
# So the programme has to come from the naming convention, which is that a
# women's event says so and a men's event says nothing:
#
#     fiba-womens-eurobasket-2027-qualifiers          women's
#     fiba-eurobasket-2029-pre-qualifiers             men's
#     fiba-u18-womens-eurobasket-2026-division-b      women's
#     fiba-basketball-world-cup-2027-americas-qualifiers   men's
#
# Checked against the official name on all 139 events currently indexed: zero
# disagreements. That's good enough to filter on, but it is a convention rather
# than a field, so `programme: all` stays available and this is the one place
# that needs changing if FIBA ever renames.
WOMENS_SLUG = re.compile(r"(?:^|-)womens?(?:-|$)")


def event_programme(slug: str) -> str:
    """'womens' or 'mens', inferred from the slug. See WOMENS_SLUG above."""
    return "womens" if WOMENS_SLUG.search(slug or "") else "mens"


def wanted_programme(slug: str, programme: str | None) -> bool:
    """Whether `slug` belongs to the programme being followed.

    `programme` of None or 'all' keeps everything, which is what the men's and
    women's sides of a national team both being interesting looks like.
    """
    if not programme or programme == "all":
        return True
    return event_programme(slug) == programme


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


def schedule_row(node: dict, slug: str) -> dict | None:
    """One fixture from an event's schedule, or None if it has no game id.

    Two status codes matter and they are not the same thing. The statistics
    status says the box score is final, which is what makes a game worth
    scraping. The result status says the score is final. A forfeit has the
    second without the first: FIBA records who won (20-0) but there is no box
    score to fetch. Those are kept as `result_only` so a forfeited placement
    game still decides a placement, instead of vanishing with the stats.
    """
    game_id = undefined_to_none(node.get("gameId"))
    if game_id is None:
        return None
    team_a = undefined_to_none(node.get("teamA")) or {}
    team_b = undefined_to_none(node.get("teamB")) or {}
    home = undefined_to_none(team_a.get("code"))
    away = undefined_to_none(team_b.get("code"))
    status = undefined_to_none(node.get("gameStatisticStatusCode"))
    result_status = undefined_to_none(node.get("gameResultStatusCode"))
    is_live = bool(undefined_to_none(node.get("isLive")))

    def score(key: str, team: dict):
        value = undefined_to_none(node.get(key))
        if value is None:
            value = undefined_to_none(team.get("score"))
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    home_score, away_score = score("teamAScore", team_a), score("teamBScore", team_b)
    # No team code = the bracket hasn't resolved yet, not missing data.
    played = bool(home and away and status == "VALID" and not is_live)

    return {
        "game_id": str(game_id),
        "home": home,
        "away": away,
        "date": (undefined_to_none(node.get("gameDateTime")) or "")[:10],
        "round": (undefined_to_none(node.get("round")) or {}).get("roundName", ""),
        "status": status,
        "result_status": result_status,
        "is_live": is_live,
        "home_score": home_score,
        "away_score": away_score,
        "played": played,
        "result_only": bool(
            home and away and not played and not is_live
            and result_status == "VALID"
            and home_score is not None and away_score is not None
            and home_score != away_score
        ),
        "url": f"{BASE}/en/events/{slug}/games/{game_id}-{home}-{away}",
    }


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
        row = schedule_row(node, slug)
        if row and row["game_id"] not in games:
            games[row["game_id"]] = row

    rows = sorted(games.values(), key=lambda r: (r["date"], r["game_id"]))
    # Say what can't be scraped and why. A future fixture is routine; an
    # unscraped game from a finished event is a hole in the data, and without
    # this it vanishes silently.
    for r in rows:
        if not r["played"]:
            log.info(
                "event %s: no box score for %s %s %s-%s (status=%s result=%s live=%s)%s",
                slug, r["game_id"], r["date"], r["home"], r["away"],
                r["status"], r["result_status"], r["is_live"],
                " — result only, e.g. a forfeit" if r["result_only"] else "",
            )
    if played_only:
        rows = [r for r in rows if r["played"]]
    log.info("event %s: %d game(s) ready to ingest", slug, len(rows))
    return rows


def event_fixtures(slug: str) -> tuple[list[str], list[dict]]:
    """One read of the schedule, split two ways: URLs of games with a final
    box score to scrape, and games with only a final result (forfeits) to
    store as a result."""
    games = event_games(slug, played_only=False)
    urls = [g["url"] for g in games if g["played"]]
    results = [g for g in games if g["result_only"]]
    log.info("event %s: %d to scrape, %d result-only", slug, len(urls), len(results))
    return urls, results


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
    programme: str | None = None,
) -> list[dict]:
    """Events the team appears in, within a date window.

    One extra fetch per candidate event, so narrow it down before calling.
    The index carries no team list — the only way to know Canada is playing is
    to read the schedule.

    `disciplines` filters on FIBA's own source tag; 'gdap' is 5x5. Pass an
    empty tuple to include 3x3 and everything else.

    `programme` is 'womens', 'mens' or None for both. It is applied before the
    per-event fetch, so narrowing it makes discovery cheaper as well as
    narrower — Canada fields both a men's and a women's senior team, and the
    men's World Cup qualifiers alone are 60 games.
    """
    team_code = team_code.upper()
    candidates = []
    for event in list_events():
        if disciplines and event["discipline"] not in disciplines:
            continue
        # Cheapest filter first: this one is free, event_teams() is a fetch.
        if not wanted_programme(event["slug"], programme):
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
    programme = cfg.get("programme")

    # Pinned entries are filtered on programme as well. A pin says "keep
    # ingesting this even once FIBA drops it from the index", not "ignore what
    # this project is about" — so the two settings can't contradict each other.
    pinned = list(dict.fromkeys(cfg.get("pinned") or []))
    slugs = [s for s in pinned if wanted_programme(s, programme)]
    if len(slugs) != len(pinned):
        dropped = [s for s in pinned if s not in slugs]
        log.warning(
            "%d pinned event(s) are not the %s programme, skipping: %s",
            len(dropped), programme, ", ".join(dropped),
        )
    log.info("%d pinned event(s)", len(slugs))

    discover = cfg.get("discover") or {}
    if discover.get("enabled"):
        found = events_for_team(
            team_code=cfg.get("team", "CAN"),
            since=discover.get("since"),
            until=discover.get("until"),
            disciplines=tuple(discover.get("disciplines") or ()),
            programme=programme,
        )
        new = [e["slug"] for e in found if e["slug"] not in slugs]
        log.info("discovery added %d event(s): %s", len(new), ", ".join(new) or "none")
        slugs.extend(new)

    return slugs
