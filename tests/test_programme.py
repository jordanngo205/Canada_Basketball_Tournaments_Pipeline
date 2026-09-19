"""The programme filter: does the slug convention actually hold?

Pure — no network, no database — so this runs on every push rather than on the
daily schedule like test_parser.py. It needs to: the filter decides what the
whole pipeline ingests, and it rests on a naming convention rather than a field
FIBA publishes, which is exactly the kind of assumption that rots quietly.

Real slugs, taken from FIBA's index. If they ever rename, this is what says so.
"""

import sys
from pathlib import Path

# Same shim as test_parser.py: runnable as `python tests/test_programme.py`
# without needing the repo installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.discover import (  # noqa: E402
    event_programme,
    select_events,
    wanted_programme,
)

WOMENS = [
    "fiba-womens-eurobasket-2027-qualifiers",
    "fiba-u18-womens-eurobasket-2026-division-b",
    "fiba-u17-womens-basketball-world-cup-2026",
    "fiba-u18-womens-americup-2026",
    "fiba-womens-olympic-pre-qualifying-tournament-2026-guadalajara-mexico",
    "fiba-womens-basketball-world-cup-2026-qualifying-tournament-istanbul-turkiye",
    "fiba-south-american-womens-championship-2026",
    "euroleague-women-26-27",
]

MENS = [
    "fiba-basketball-world-cup-2027-americas-qualifiers",
    "fiba-eurobasket-2029-pre-qualifiers",
    "fiba-u18-eurobasket-2026",
    "fiba-basketball-world-cup-2027-african-qualifiers",
    "athens-challenger-2026-d3dc87bf-8cab-4223-9850-035946faf352",
]


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    return ok


def main() -> int:
    failures = 0

    print("women's events classify as womens")
    for slug in WOMENS:
        failures += not check(slug, event_programme(slug), "womens")

    print("\nmen's events classify as mens")
    for slug in MENS:
        failures += not check(slug, event_programme(slug), "mens")

    # 'euroleague-women-26-27' is the case that makes the regex worth having:
    # singular 'women', no 's', and not at the front of the slug.
    print("\nthe filter keeps what it should")
    failures += not check(
        "womens keeps a women's event",
        wanted_programme("fiba-u18-womens-americup-2026", "womens"), True)
    failures += not check(
        "womens drops a men's event",
        wanted_programme("fiba-basketball-world-cup-2027-americas-qualifiers", "womens"), False)
    failures += not check(
        "mens drops a women's event",
        wanted_programme("fiba-u18-womens-americup-2026", "mens"), False)
    failures += not check(
        "'all' keeps both",
        [wanted_programme(s, "all") for s in (MENS[0], WOMENS[0])], [True, True])
    failures += not check(
        "unset keeps both",
        [wanted_programme(s, None) for s in (MENS[0], WOMENS[0])], [True, True])

    # A pin is "keep ingesting this after FIBA drops it from the index", not an
    # override of what the project is about. The two settings must not be able
    # to contradict each other.
    print("\nthe config's pins agree with its programme")
    config = {
        "team": "CAN",
        "programme": "womens",
        "pinned": WOMENS[:2] + [MENS[0]],
        "discover": {"enabled": False},
    }
    failures += not check("a men's pin is dropped under programme: womens",
                          select_events(config), WOMENS[:2])

    print(f"\n{'FAILED' if failures else 'passed'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
