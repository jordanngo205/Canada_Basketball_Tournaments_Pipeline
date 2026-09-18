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

168 games across the five tournaments Canada played in:

```
  60  FIBA Basketball World Cup 2027 Americas Qualifiers
  56  FIBA U17 Women's Basketball World Cup
  22  FIBA U18 Women's AmeriCup
  15  FIBA Women's Basketball World Cup 2026 Qualifying — Türkiye
  15  FIBA Women's Olympic Pre-Qualifying Tournament
```

| Table | Grain | Rows |
|---|---|---|
| `mart_player_leaders` | player per competition | 796 |
| `mart_team_efficiency` | team per game | 336 |
| `mart_shot_zones` | team × zone per competition | 270 |
| `mart_four_factors` | team per competition | 54 |
| `stg_period_scores` | period per game | 676 |
| `mart_standings` | team per group | 66 |
| `int_shots` | one field goal attempt | 22,335 |
| `int_player_minutes` | player per game | 4,024 |

Everything's per 100 possessions rather than per game — FIBA sides vary enough
in pace that raw totals flatter the fast ones:

```
grp rk team  record  diff   ortg   drtg   net     overall
B   1  CAN    3-0     +68   101.3   72.6  +28.7     5-0
B   2  MEX    2-1     +11    89.5   83.5   +6.0     3-2
A   1  NZL    2-1     +27    89.7   75.3  +14.4     2-2
```

The pinned tournaments live in `config/events.yml`; discovery adds anything new
Canada turns up in. The ingest task is mapped with `.expand()`, so Airflow runs
one task instance per tournament and a failure in one leaves the rest green.

## How the ingest works

Four steps, and the split between them is the point — `ingest/fiba.py` imports
no pandas and does no arithmetic, because everything it touches has to stay
replayable.

**1. Which games are finished?** `discover.py` reads the tournament's schedule
page and returns a URL per game. Don't use the score to decide if a game is
done — a live game has a score too, and you'd freeze a half-finished box score
into the warehouse. `gameStatisticStatusCode` flips `EMPTY` → `VALID` when the
stats are final. That's the one to trust.

**2. Fetch the page.** Plain `requests.get`. FIBA's pages are Next.js App Router
and fully server-rendered, so one GET returns the box score, both rosters, and
play-by-play with shot coordinates. No browser, no Selenium. Retries are linear
rather than exponential — the failures are short connection resets under
tournament load, not rate limiting.

**3. Pull the JSON out.** The payload ships as React Server Component wire
format: a run of `self.__next_f.push([1, "<id>:<json>"])` script tags. Strip the
chunk id, parse each one, and find the node carrying `game`, `playersTeamA` and
`gameDetails` — nothing else on the page has all three.

**4. Store it untouched.** Straight into `raw.raw_games` as JSONB, with the URL
and fetch time. Before inserting, compare against the newest stored copy and
skip if identical, so a daily sweep over a finished tournament writes nothing.
The primary key is `(game_id, fetched_at)`, so a corrected box score lands as a
new row and `raw.latest_games` serves the most recent one downstream.

### Three things about FIBA's payload that cost me real time

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

### What the tests actually caught

Five of these, and none were bad data from FIBA. Every one was my model assuming
a simpler world than the real tournament calendar.

**Period scores were cumulative, not per-quarter.** Quarter sums came to 190
against a 61-point final. I'd read the wrong one of the two sources. Because raw
was already stored, the fix was one SQL file and a rebuild — no re-scraping.

**My rebound test was wrong, not the data.** I asserted players' rebounds sum to
the team's. It failed on every game by 2 to 8. Team rebounds — ball out off the
defence, missed last free throw — belong to the team and no player. The fix was
to assert direction rather than delete the test.

**Standings duplicated once knockout games arrived.** Passed on three group-phase
games, broke on the full tournament: semi-finals and the final carry no group
code, so every team that qualified got a second row.

**Then standings broke again at five tournaments.** The World Cup 2027 Americas
Qualifiers runs *two* group phases — Canada is in group B and then group F. Both
rows are legitimate; the grain is per group, not per competition.

**Players were splitting in two.** FIBA spells the same person differently
between games in one tournament: id 202510 is "Pako Saldivar" in two games and
"Pako Cruz" in six. Grouping on the name halved his stats without erroring.
Group on the id; treat the name as an attribute.

The pattern across all five: logic that is correct for the data you happen to
have, and wrong for the data that arrives later. None would have been visible by
looking at a dashboard — every individual number looked perfectly reasonable.

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

## Shot charts and minutes

Both come out of the play-by-play, and both needed reverse engineering because
FIBA documents neither.

**Shot coordinates.** `x` runs 0–280 across the court with centre near 140, and
`y` is *distance from the basket* rather than position along it — both teams'
shots land on one half court. Zones were then derived from where the shooting
actually breaks: 54% inside y=40, 40% from 40–59, then a cliff to 30% beyond.
Threes split on `x`, not `y`. The result reproduces the shot-selection curve you
would expect, which is the best evidence the reading is right:

```
Rim                1.07 points per attempt   53.6% FG
Corner 3           0.91                      30.2%
Above the break 3  0.90                      30.0%
Paint              0.80                      39.9%
Mid-range          0.59                      29.7%
```

**Minutes.** FIBA leaves `MP` null, so they're reconstructed by walking the
substitution stream. Getting it right took three attempts. Crediting a full
period to anyone who recorded an action in it made things *worse* — mean error
4.5 minutes to 13.8. The real bug was that a starter who plays all of Q1 and
comes off in Q2 has their first event in Q2, so falling back to "start of this
period" silently discarded the whole first quarter. Five starters is 50 minutes
a game.

Mean absolute error against the 200 team-minutes a game must produce is now
**0.12 minutes** across 336 team-games, worst case 4.9.

## Backfills

`fiba_backfill` is manual-trigger only and deliberately *not* date-partitioned.
A textbook backfill replays a date range, which assumes the source can tell you
what it looked like then — FIBA can't, a game page serves only the current box
score. Two modes that do make sense:

- **`rebuild_only: true`** (default) — re-run every model and test against raw
  payloads already stored, then re-export. No network. This is how a corrected
  formula reaches the marts, and it takes seconds.
- **`events: [...]`** — re-scrape named tournaments first, for when FIBA
  corrects a box score after the fact.

## Still to do

- Publish the dashboard to GitHub Pages (the page and its data are in `docs/`,
  Pages just isn't switched on for this repo yet)
- Run the pipeline somewhere that isn't a laptop. Airflow only fires while
  Docker is up locally; a hosted Postgres plus a GitHub Actions trigger would
  make it genuinely unattended
