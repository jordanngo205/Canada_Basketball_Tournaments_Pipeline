# Canada Basketball Tournaments — Data Pipeline

Scrapes FIBA games, stores the raw payloads, transforms them with dbt, checks the
numbers, and spits out clean JSON for a dashboard. Postgres and Airflow run in
Docker, so `docker compose up` is the whole setup.

```
fiba.basketball ──▶ raw.raw_games ──▶ dbt models ──▶ marts ──▶ JSON
   (plain GET)         (JSONB)          (SQL)      (tables)  (dashboard)
                           ▲                ▲
                     Airflow runs it    tests gate it
```

I built this because I'd been pulling FIBA box scores by hand and then with a
single script, and I wanted the version with a proper raw layer, real SQL
transforms and tests that fail loudly.

## Quick start

```bash
cp .env.example .env          # edit the passwords
docker compose up -d
open http://localhost:8080    # admin / admin, then trigger the DAG
```

Warehouse is on port 5433 from the host (5432 left free for a local Postgres):

```bash
psql postgresql://fiba:fiba@localhost:5433/warehouse
```

Everything also runs without Airflow if you'd rather poke at it directly:

```bash
python tests/test_parser.py                     # live check, 4 games
python -m ingest.discover <event-slug>          # what's in a tournament
python -m ingest.load <game-url> ...            # scrape and store
python -m reports.export_marts                  # write out/*.json
```

**Code lives inside the image**, so `docker compose build` after changing any
Python or SQL. Only `out/` is mounted, since the host needs the JSON back.

## What comes out

19 games across two tournaments:

| Table | Grain | Rows |
|---|---|---|
| `mart_standings` | team per competition | 10 |
| `mart_player_leaders` | player per competition | 117 |
| `mart_team_efficiency` | team per game | 32 |
| `stg_player_box` | player per game | 376 |

Everything's per 100 possessions rather than per game — FIBA sides vary enough
in pace that raw totals flatter the fast ones:

```
grp rk team  record  diff   ortg   drtg   net     overall
B   1  CAN    3-0     +68   101.3   72.6  +28.7     5-0
B   2  MEX    2-1     +11    89.5   83.5   +6.0     3-2
A   1  NZL    2-1     +27    89.7   75.3  +14.4     2-2
```

## Scraping FIBA

Their pages are Next.js App Router and fully server-rendered, so a plain
`requests.get` gets you everything — box score, rosters, play-by-play with shot
coordinates. No browser needed.

It comes back as RSC wire format: `self.__next_f.push([1, "<id>:<json>"])` script
tags. Three things in there cost me real time:

- **`gameDetails.teamA` and `teamB` aren't data.** They're pointers — literal
  strings like `$1c:props:gameDetails:c:0:Stats`. The actual records are at
  `gameDetails.c[0]` (home) and `c[1]` (away).
- **There are two sources of period scores and they mean different things.**
  `playByPlay` gives running totals; `gameDetails.quartersScores` gives points
  scored in the period. Canada's quarters in game 135067 read 22/19/17/14 in one
  and 22/40/59/72 in the other, against a 72-point final.
- **Missing values are the string `$undefined`, not null.** Every nullable field
  needs a `nullif` guard.

## Why it's laid out this way

**Raw is append-only and never edited.** FIBA's response goes into
`raw.raw_games` as JSONB with nothing done to it. When a transform turns out to
be wrong, I change the model and re-run instead of re-scraping. That's why
`ingest/fiba.py` has no pandas import and does no arithmetic.

This paid off the same day I wrote it — I'd read those period scores as
per-quarter when they're cumulative, and fixing it was one SQL file and a
rebuild rather than re-collecting every game.

**Postgres holds the warehouse and Airflow's metadata in separate databases**, so
a `dbt run` can't collide with the scheduler's own state.

**`dbt build`, not `run` then `test`** — build interleaves them, so a model that
fails its test never feeds the one below it.

**Ingest retries, dbt doesn't.** FIBA drops connections during tournaments and
that's a flake worth retrying. A failing test is a bug, and retrying it three
times just delays finding out.

**Airflow over cron is arguable and I'd say so in an interview.** For this scope
cron would work fine — my other dashboard runs on GitHub Actions cron and that's
the right call there. Airflow earns it here because ingest takes ~60s and
`dbt build` takes ~3s: with one script, a failing test means re-scraping 15 games
to get back to the step that broke.

## Tests

56 of them. The generic `not_null` / `unique` / range ones are in the
`_schema.yml` files. The interesting ones are in `dbt/tests/` — basketball
arithmetic that can't be false, so a failure is a bug rather than a threshold
someone guessed at.

| Test | What it asserts |
|---|---|
| `assert_quarter_scores_sum_to_final` | Final score = sum of period points |
| `assert_points_equal_shot_making` | `PTS = 2·FG2M + 3·FG3M + FTM` |
| `assert_player_totals_match_team` | Players account for the team's points and assists |
| `assert_shooting_splits_consistent` | Makes ≤ attempts, `FGM = FG2M + FG3M` |
| `assert_win_loss_balances` | Wins and losses balance across a competition |
| `assert_both_teams_present` | Exactly two team rows per game |

**Rebounds are deliberately not checked for equality**, and working out why took
a while. A team rebound — ball out off the defence, missed last free throw —
belongs to the team and to no player, so the team total legitimately runs higher
than the sum of its players. The gap was 2 to 8 across four games. The test
asserts direction instead, which still catches a roster row that didn't parse.

Two bugs these actually caught:

- Period scores misread as per-quarter when they were cumulative (190 ≠ 61).
- `mart_standings` passed on three group games and broke on the full tournament.
  Semi-finals and the final carry no group code, so every team that qualified got
  a second row. Standings are group-phase only now, with the overall record in
  separate columns.

## Layout

```
ingest/fiba.py       fetch + parse. no pandas, no SQL, no maths
ingest/discover.py   event slug -> game URLs
ingest/load.py       payload -> raw.raw_games, unchanged
sql/                 database + raw schema, applied on first boot and in CI
dbt/models/staging/  JSONB unpacked into columns
dbt/models/marts/    standings, leaders, efficiency
dbt/tests/           the arithmetic tests above
dags/                discover -> ingest -> build -> export, daily
reports/             marts -> JSON
tests/               live parser check + fixtures for CI
```

## CI

Every push runs `dbt build` against a throwaway Postgres seeded from three
committed fixture games (two tournaments, one overtime). The exported JSON gets
uploaded as a build artifact.

The live parser check runs daily instead of on every push — a FIBA outage
shouldn't turn someone's commit red, and what it's really for is telling me FIBA
changed their page before a tournament run finds out the hard way.

## macOS gotchas

Both of these cost me hours and neither is obvious from the error:

- **Don't keep this in `~/Documents`.** iCloud syncs it and leaves evicted files
  as placeholders. Docker can't trigger the download, so reads fail with
  `OSError: [Errno 5] Input/output error` while `ls` still shows the right file
  sizes. Writes can fail with `[Errno 35] Resource deadlock avoided`. Anywhere
  outside iCloud is fine.
- **Docker needs folder permission** — System Settings → Privacy & Security →
  Files and Folders.

## Still to do

- Fan out across multiple tournaments in one DAG (dynamic task mapping) instead
  of one event slug per run
- Point the existing dashboard at these JSON files
- Minutes played — FIBA leaves `MP` null in the box score block, so it has to
  come from play-by-play substitutions before any per-minute stat works
