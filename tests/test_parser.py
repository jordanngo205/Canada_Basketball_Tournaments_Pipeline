"""Smoke test for the RSC parser against live FIBA pages.

Run before trusting an ingest run, and after any change to ingest/fiba.py:

    python3 tests/test_parser.py

These are live-network tests on purpose. The thing most likely to break this
pipeline is FIBA changing its page structure, and that is exactly what a
fixture-based test would hide. Fixtures belong in the dbt layer, where the
input is our own raw table and the logic under test is ours.

Cases span two tournaments so a structure change specific to one event's
templates shows up as a partial failure rather than a total one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.fiba import GameNotFound, scrape_game  # noqa: E402

PQT = (
    "https://www.fiba.basketball/en/events/"
    "fiba-womens-olympic-pre-qualifying-tournament-2026-guadalajara-mexico/games/"
)
WWC = (
    "https://www.fiba.basketball/en/events/"
    "fiba-womens-basketball-world-cup-2026/games/"
)

# (url, expected home score, expected away score) — scores taken from FIBA's
# own published schedule, so a mismatch means our parse drifted, not the game.
CASES = [
    (PQT + "135061-BRA-SSD", 61, 63),
    (PQT + "135067-CAN-SEN", 72, 57),
    (WWC + "128116-JPN-MLI", 102, 97),
    (WWC + "128117-ESP-GER", 83, 53),
]


def check(url: str, want_home: int, want_away: int) -> list[str]:
    """Return a list of failure messages for one game; empty means it passed."""
    fails: list[str] = []
    try:
        raw = scrape_game(url)
    except (GameNotFound, RuntimeError) as exc:
        return [f"fetch/parse raised {type(exc).__name__}: {exc}"]

    s = raw.summary()

    if s["home_score"] != want_home or s["away_score"] != want_away:
        fails.append(
            f"score {s['home_score']}-{s['away_score']} != expected "
            f"{want_home}-{want_away}"
        )

    if not raw.game_id.isdigit():
        fails.append(f"game_id {raw.game_id!r} is not numeric")

    if s["players"] < 20:
        fails.append(f"only {s['players']} players; expected both full rosters")

    if s["pbp_actions"] < 200:
        fails.append(
            f"only {s['pbp_actions']} play-by-play actions; a full game runs 400+"
        )

    # The identity that makes this data trustworthy: per-period points must add
    # up to the final score. If FIBA reshapes the period nesting, this catches
    # it even when the top-level score still parses.
    points = raw.period_points()
    if points:
        qa = sum(a for a, _ in points.values())
        qb = sum(b for _, b in points.values())
        if (qa, qb) != (want_home, want_away):
            fails.append(
                f"period points sum to {qa}-{qb} != final {want_home}-{want_away} "
                f"(periods: {','.join(points)})"
            )

        # And the last period's running total is the final score by definition.
        last_a, last_b = list(raw.period_scores().values())[-1]
        if (last_a, last_b) != (want_home, want_away):
            fails.append(
                f"last period cumulative {last_a}-{last_b} != final "
                f"{want_home}-{want_away}"
            )

        if any(a < 0 or b < 0 for a, b in points.values()):
            fails.append(f"negative period points: {points}")
    else:
        fails.append("no period blocks found")

    return fails


def main() -> int:
    failed = 0
    for url, home, away in CASES:
        label = url.rsplit("/", 1)[-1]
        problems = check(url, home, away)
        if problems:
            failed += 1
            print(f"FAIL  {label}")
            for p in problems:
                print(f"        {p}")
        else:
            raw = scrape_game(url)
            s = raw.summary()
            print(
                f"ok    {label:<18} {s['home']} {s['home_score']}-"
                f"{s['away_score']} {s['away']}  "
                f"{s['pbp_actions']:>4} actions  "
                f"{len(s['periods'])} periods  {s['players']} players"
            )

    print(f"\n{len(CASES) - failed}/{len(CASES)} games parsed correctly")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
