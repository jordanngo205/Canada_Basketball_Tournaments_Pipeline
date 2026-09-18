# Canada Basketball Tournaments — Data Pipeline

A scheduled data pipeline for FIBA basketball: scrape games, store the payloads
untouched, transform them with SQL, test the numbers, and publish clean tables
for a dashboard.

```
fiba.basketball ──▶ raw.raw_games ──▶ dbt models ──▶ analytics marts ──▶ JSON
   (HTTP GET)          (JSONB)          (SQL)          (tables)        (dashboard)
                            ▲                 ▲
                      Airflow schedules   dbt tests gate
```

Postgres and Airflow run in Docker Compose. One command brings the whole thing
up; nothing needs installing on the host.

## What it produces

From 19 games across two tournaments:

| Table | Grain | Rows |
|---|---|---|
| `mart_standings` | team per competition | 10 |
| `mart_player_leaders` | player per competition | 117 |
| `mart_team_efficiency` | team per game | 32 |
| `stg_player_box` | player per game | 376 |
| `stg_period_scores` | period per game | 65 |

Ratings are per 100 possessions rather than per game, because pace varies enough
between FIBA sides that raw totals mislead:

```
grp rk team  record  diff   ortg   drtg   net     overall
B   1  CAN    3-0     +68   101.3   72.6  +28.7     5-0
B   2  MEX    2-1     +11    89.5   83.5   +6.0     3-2
A   1  NZL    2-1     +27    89.7   75.3  +14.4     2-2
```

## Why it is built this way

**Raw is immutable and stores the payload untouched.** FIBA's response lands in
`raw.raw_games` as JSONB with no cleaning applied. When a transform turns out to
be wrong, the fix is a model change and a re-run — never a re-scrape. That is
why `ingest/fiba.py` imports no pandas and does no arithmetic.

This paid for itself during the build: FIBA's play-by-play period scores are
*running totals*, not per-period points, and I had read them as per-period.
Because the payloads were already stored, correcting it was one SQL file and a
rebuild rather than re-collecting every game.

**Postgres is the warehouse, not just Airflow's metadata store.** They are
separate databases on one server so a `dbt run` cannot contend with the
scheduler's own state.

**`dbt build`, not `dbt run` then `dbt test`.** `build` interleaves models and
their tests, so a model whose output fails a test never becomes the input to the
model below it.

**Ingest retries; transforms do not.** FIBA drops connections under tournament
load — a flake worth retrying. A failing dbt test is a bug, and retrying it
three times only delays finding out.

**Airflow over cron is a deliberate, arguable call.** For this scope cron would
work. Airflow earns its place because ingest takes ~60s and `dbt build` takes
~3s: with a single script, a failing test means re-scraping 15 games to get back
to the step that broke. Airflow re-runs the failed task alone.

## Getting the data out of FIBA

The pages are Next.js App Router and fully server-rendered, so a plain
`requests.get` returns everything — box score, rosters, and play-by-play with
shot coordinates. No browser, no Selenium.

The payload arrives as React Server Component wire format:
`self.__next_f.push([1, "<chunk-id>:<json>"])` script tags. Three things about
it cost real time to discover:

- **`gameDetails.teamA` and `teamB` are pointers, not data.** They hold strings
  like `$1c:props:gameDetails:c:0:Stats`. The resolved records live at
  `gameDetails.c[0]` (home) and `c[1]` (away). Every model reads `c`.
- **Two period-score sources disagree in meaning.**
  `playByPlay.items.<period>.scoreA` is the running total at the end of that
  period; `gameDetails.quartersScores` is the points scored *in* it. Canada's
  Q1–Q4 in game 135067 read 22/19/17/14 in one and 22/40/59/72 in the other,
  against a 72-point final. The models use `quartersScores`.
- **Absent values are the string `$undefined`, not null**, so every nullable
  field needs a `nullif(..., '$undefined')` guard.

## The tests

Generic `not_null`, `unique`, `relationships` and range tests live in the
`_schema.yml` files. The ones worth pointing at are the singular tests in
`dbt/tests/`, which assert basketball arithmetic that cannot be false — so a
failure is unambiguously a bug, not a threshold someone guessed at.

| Test | Assertion |
|---|---|
| `assert_quarter_scores_sum_to_final` | Each team's final score equals the sum of its per-period points — reconciles two independently-parsed parts of the payload. |
| `assert_points_equal_shot_making` | `PTS = 2·FG2M + 3·FG3M + FTM`, per player. |
| `assert_player_totals_match_team` | Players account for exactly the team's points and assists. |
| `assert_shooting_splits_consistent` | Makes ≤ attempts; `FGM = FG2M + FG3M`; `REB = OR + DR`. |
| `assert_win_loss_balances` | Wins and losses balance across a competition. |
| `assert_both_teams_present` | Every game contributes exactly two team rows. |

**Rebounds are deliberately not reconciled by equality.** A team rebound — the
ball going out off the defence, a missed final free throw — is credited to the
team and to no player, so a team's total legitimately exceeds the sum of its
players. Across four games in two tournaments that gap ran 2 to 8. The test
asserts direction (`team_reb >= sum(player_reb)`) instead, which still catches a
roster row that failed to parse.

Two bugs these caught during the build:

- Period scores misread as per-period when they were cumulative (190 ≠ 61).
- `mart_standings` passed on three group-phase games and broke on the full
  tournament: semi-finals and the final carry no group code, so every team that
  qualified got a second row. Standings now cover the group phase only, with the
  overall record in separate columns.

## Running it

```bash
cp .env.example .env          # then edit the passwords
docker compose up -d          # Postgres + Airflow
open http://localhost:8080    # trigger the DAG from the UI
```

The warehouse is reachable from the host on port **5433** (5432 is left free for
any local Postgres):

```bash
psql postgresql://fiba:fiba@localhost:5433/warehouse
```

Ingest and the parser also run standalone, without Airflow:

```bash
python tests/test_parser.py                     # live check against 4 games
python -m ingest.discover <event-slug>          # list a tournament's games
python -m ingest.load <game-url> [<game-url>]   # scrape and store
python -m reports.export_marts                  # write out/*.json
```

**Code ships inside the image**, so run `docker compose build` after changing
any Python or SQL. Only `out/` is bind-mounted, since the host needs the JSON.

### Two environment notes for macOS

Both cost hours during setup and neither is obvious from the error message:

- **Do not keep this project in `~/Documents`.** It is synced by iCloud, which
  keeps evicted files as placeholders. Docker cannot trigger the download, so
  reads fail with `OSError: [Errno 5] Input/output error` while `ls` still shows
  correct sizes, and writes can fail with `[Errno 35] Resource deadlock avoided`.
  `~/dev` or anywhere outside iCloud is fine.
- **Docker needs permission to read the folder** — System Settings → Privacy &
  Security → Files and Folders.

## Layout

```
ingest/fiba.py       fetch + RSC parse. No pandas, no SQL, no arithmetic.
ingest/discover.py   event slug -> URLs of games with final stats
ingest/load.py       parsed payload -> raw.raw_games, unchanged
sql/init/            database and raw schema, applied on first boot and in CI
dbt/models/staging/  JSONB unpacked into typed columns
dbt/models/marts/    finished tables: standings, leaders, efficiency
dbt/tests/           the singular tests above
dags/                the scheduled discover -> ingest -> build -> export DAG
reports/             mart tables -> JSON for the dashboard
tests/               live parser smoke test + committed fixtures for CI
```

## CI

Every push runs `dbt build` against a real throwaway Postgres, seeded from
committed fixtures — three real games across two tournaments, one of which went
to overtime. The exported JSON is uploaded as a build artifact.

The live parser check runs daily rather than on every push: a FIBA outage should
not turn someone's commit red, and its real job is to tell us FIBA changed their
page before a tournament run discovers it.
