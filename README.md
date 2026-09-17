# FIBA Scouting Report Pipeline

Automates a scouting workflow that used to be manual — pull box scores, compute
KPIs, produce a PDF — as a scheduled pipeline with a raw layer, SQL transforms,
data quality tests and CI.

```
fiba.basketball ──▶ raw.raw_games ──▶ dbt models ──▶ analytics marts ──▶ PDF
   (HTTP GET)         (JSONB)          (SQL)          (tables)        (ReportLab)
                          ▲                 ▲
                    Airflow schedules   dbt tests gate
```

## Why it is built this way

**Raw is immutable and stores the payload untouched.** FIBA's response lands in
`raw.raw_games` as JSONB with no cleaning applied. When a KPI turns out to be
wrong, the fix is a dbt model change and a re-run — never a re-scrape. That
single decision is what makes the pipeline replayable, and it is why
`ingest/fiba.py` has no pandas import and no arithmetic in it.

**Postgres is the warehouse, not just Airflow's metadata store.** They are
separate databases on one server so a `dbt run` can never contend with the
scheduler's own state.

**dbt `build` rather than `run` then `test`.** `build` interleaves models and
their tests, so a model whose output fails a test never becomes the input to
the model below it.

**Ingest retries; transforms do not.** FIBA drops connections under tournament
load, which is a flake worth retrying. A failing dbt model is a bug, and
running it three more times only delays finding out.

## Getting the data out of FIBA

The pages are Next.js App Router, fully server-rendered, so a plain
`requests.get` returns everything — box score, rosters, and play-by-play with
shot coordinates. No browser, no Selenium.

The data arrives as React Server Component wire format: a series of
`self.__next_f.push([1, "<chunk-id>:<json>"])` script tags. Two things about it
are worth knowing before reading the code, because both cost time to discover:

- **`gameDetails.teamA` and `teamB` are pointers, not data.** They hold strings
  like `$1c:props:gameDetails:c:0:Stats`. The resolved records live at
  `gameDetails.c[0]` (home) and `gameDetails.c[1]` (away). Every model reads
  `c`; nothing reads `teamA`/`teamB`.
- **Two sources of period scores disagree in meaning.**
  `playByPlay.items.<period>.scoreA` is the *running total* at the end of that
  period. `gameDetails.quartersScores` is the points scored *in* the period.
  Canada's Q1–Q4 in game 135067 read 22/19/17/14 in one and 22/40/59/72 in the
  other, against a 72-point final. The models use `quartersScores`.

Absent values serialise as the literal string `$undefined`, not `null`, so
every nullable field needs a `nullif(..., '$undefined')` guard.

## The tests that matter

Generic `not_null` and `unique` tests are in `dbt/models/staging/_schema.yml`.
The ones worth pointing at are the singular tests in `dbt/tests/`, which assert
basketball arithmetic that cannot be false — so a failure is unambiguously a
parsing bug rather than a threshold someone guessed at.

| Test | Assertion |
|---|---|
| `assert_quarter_scores_sum_to_final` | Each team's final score equals the sum of its per-period points. Reconciles two independently-parsed parts of the payload. |
| `assert_points_equal_shot_making` | `PTS = 2·FG2M + 3·FG3M + FTM`, per player. |
| `assert_player_totals_match_team` | Players account for exactly the team's points and assists. |
| `assert_shooting_splits_consistent` | Makes ≤ attempts; `FGM = FG2M + FG3M`; `REB = OR + DR`. |

**Rebounds are deliberately not reconciled by equality.** A team rebound — the
ball going out off the defence, a missed final free throw that deadballs — is
credited to the team and to no player, so a team's total legitimately exceeds
the sum of its players. Measured across four games in two tournaments that gap
ran 2 to 8. The test asserts the direction (`team_reb >= sum(player_reb)`)
instead, which still catches a roster row that failed to parse.

## Running it

```bash
cp .env.example .env          # then edit the passwords
docker compose up -d          # Postgres + Airflow
open http://localhost:8080    # admin / whatever you set
```

The warehouse is reachable from the host on port **5433** (5432 is left free
for any local Postgres):

```bash
psql postgresql://fiba:fiba@localhost:5433/warehouse
```

Without Docker, the ingest and parser run standalone:

```bash
python3 tests/test_parser.py                    # live check against 4 games
python3 -m ingest.discover <event-slug>         # list a tournament's games
python3 -m ingest.load <game-url> [<game-url>]  # scrape and store
```

## Layout

```
ingest/fiba.py       fetch + RSC parse. No pandas, no SQL, no arithmetic.
ingest/discover.py   event slug -> the URLs of games with final stats
ingest/load.py       parsed payload -> raw.raw_games, unchanged
sql/init/            warehouse + raw schema, run once on first boot
dbt/models/staging/  JSONB unpacked into typed columns
dbt/tests/           the singular tests above
dags/                the scheduled ingest -> build -> report DAG
tests/               live parser smoke test + committed fixtures for CI
```

## Status

Verified working: the parser (4 games across 2 tournaments, including an
overtime game), event discovery (15/15 games found for the pre-qualifier), and
all four singular test assertions, checked against live payloads.

Not yet built: the intermediate and mart models, the ReportLab report
generator, and the `MP` (minutes played) column — FIBA leaves it null in the
box-score block, so minutes have to be derived from play-by-play substitutions
before any per-minute KPI or a "team minutes sum to 200" test can work.
