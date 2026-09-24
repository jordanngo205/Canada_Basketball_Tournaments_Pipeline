"""Reading one fixture off an event's schedule: scrape it, store its result, or
leave it.

Pure — no network, no database — so it runs on every push. It pins down the
two status codes the ingest rests on: the statistics status (a box score to
scrape) and the result status (a final score). A forfeit has the second
without the first, and if that ever stops being recognised a forfeited
placement game silently stops deciding a placement.

Nodes are shaped like FIBA's schedule entries, trimmed to the fields read.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.discover import schedule_row  # noqa: E402

SLUG = "fiba-u19-womens-basketball-world-cup-2025"


def node(**over):
    base = {
        "gameId": 131000,
        "teamA": {"code": "CAN"},
        "teamB": {"code": "ARG"},
        "gameDateTime": "2025-07-19T10:00:00",
        "round": {"roundName": "Class 5-6"},
        "gameStatisticStatusCode": "VALID",
        "gameResultStatusCode": "VALID",
        "isLive": False,
        "teamAScore": 71,
        "teamBScore": 64,
    }
    base.update(over)
    return base


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    return ok


def flags(row):
    return (row["played"], row["result_only"])


def main() -> int:
    failures = 0

    print("which fixtures are scraped, stored as a result, or left")
    failures += not check("a finished game is scraped",
                          flags(schedule_row(node(), SLUG)), (True, False))
    failures += not check("a forfeit is stored as a result",
                          flags(schedule_row(node(gameStatisticStatusCode="EMPTY",
                                                  teamAScore=20, teamBScore=0), SLUG)),
                          (False, True))
    failures += not check("a live game is neither, even with a score",
                          flags(schedule_row(node(isLive=True, gameStatisticStatusCode="EMPTY",
                                                  gameResultStatusCode="EMPTY"), SLUG)),
                          (False, False))
    failures += not check("a future game is neither",
                          flags(schedule_row(node(gameStatisticStatusCode="EMPTY",
                                                  gameResultStatusCode="EMPTY",
                                                  teamAScore="$undefined",
                                                  teamBScore="$undefined"), SLUG)),
                          (False, False))
    failures += not check("an unresolved bracket slot is neither",
                          flags(schedule_row(node(teamB={"code": "$undefined"},
                                                  gameStatisticStatusCode="EMPTY"), SLUG)),
                          (False, False))
    failures += not check("a result with no winner is not stored",
                          flags(schedule_row(node(gameStatisticStatusCode="EMPTY",
                                                  teamAScore=0, teamBScore=0), SLUG)),
                          (False, False))

    print("\nwhat a stored result carries")
    row = schedule_row(node(gameStatisticStatusCode="EMPTY", teamAScore=0, teamBScore=20), SLUG)
    failures += not check("scores, round and teams",
                          (row["home"], row["away"], row["home_score"], row["away_score"],
                           row["round"], row["date"]),
                          ("CAN", "ARG", 0, 20, "Class 5-6", "2025-07-19"))
    failures += not check("score falls back to the team record",
                          schedule_row(node(teamAScore="$undefined",
                                            teamA={"code": "CAN", "score": "20"}), SLUG)["home_score"],
                          20)
    failures += not check("no game id, no row", schedule_row(node(gameId="$undefined"), SLUG), None)

    print(f"\n{'FAILED' if failures else 'passed'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
