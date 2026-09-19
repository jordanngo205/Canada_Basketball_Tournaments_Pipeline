"""Render the Canada Basketball dashboard from the warehouse.

This does not invent a dashboard. It uses the template already published at
jordanngo205.github.io/Canada-Basketball-Tournaments verbatim — vendored at
reports/template/dashboard_template.html — and swaps that file's
`// %%DATA_START%% … // %%DATA_END%%` block for data queried out of Postgres
instead of scraped by the original script.

So the output is the same dashboard, fed by a tested pipeline rather than a
single script, plus the sections that one doesn't have (shot chart, four
factors), appended as extra zones.

The variable names and record shapes below are not arbitrary: they are what
the template's own JavaScript reads. Renaming any of them silently empties a
section, so the SQL aliases match the original CSV headers exactly, quirks
and all — "EFG%", "TO/Poss", "DRB rt", "AST/FG%".

    python -m reports.build_dashboard --competition "FIBA Women's Olympic..."
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent / "template" / "dashboard_template.html"
START, END = "// %%DATA_START%%", "// %%DATA_END%%"
DEFAULT_DSN = "postgresql://fiba:fiba@localhost:5433/warehouse"


# IOC three-letter code -> ISO 3166-1 alpha-2, for flag images. Lifted from the
# original scraper so the same teams resolve to the same flags.
FLAG_MAP = {
    "ARG": "ar", "AUS": "au", "BAH": "bs", "BEL": "be", "BRA": "br", "CAN": "ca",
    "CHI": "cl", "CHN": "cn", "CIV": "ci", "COL": "co", "CRC": "cr", "CRO": "hr",
    "CUB": "cu", "CZE": "cz", "DOM": "do", "EGY": "eg", "ESP": "es", "FRA": "fr",
    "GBR": "gb", "GER": "de", "GRE": "gr", "HUN": "hu", "ITA": "it", "JAM": "jm",
    "JPN": "jp", "KOR": "kr", "LAT": "lv", "LTU": "lt", "MEX": "mx", "MLI": "ml",
    "NCA": "ni", "NED": "nl", "NGA": "ng", "NZL": "nz", "PAN": "pa", "PAR": "py",
    "PHI": "ph", "POL": "pl", "PUR": "pr", "SEN": "sn", "SLO": "si", "SRB": "rs",
    "SSD": "ss", "SVK": "sk", "SWE": "se", "TUR": "tr", "URU": "uy", "USA": "us",
    "VEN": "ve", "ANG": "ao", "FIN": "fi", "ISR": "il", "POR": "pt", "UKR": "ua",
}

TEAM_COLORS = {
    "ARG": "#75AADB", "AUS": "#00843D", "BAH": "#00778B", "BEL": "#FDDA24",
    "BRA": "#009C3B", "CAN": "#D80621", "CHI": "#D52B1E", "CHN": "#EE1C25",
    "CIV": "#F77F00", "COL": "#FCD116", "CRC": "#002B7F", "CRO": "#E62020",
    "CUB": "#002A8F", "CZE": "#11457E", "DOM": "#002D62", "EGY": "#CE1126",
    "ESP": "#AA151B", "FRA": "#002395", "GBR": "#012169", "GER": "#DD0000",
    "GRE": "#0D5EAF", "HUN": "#477050", "ITA": "#0064AA", "JAM": "#009B3A",
    "JPN": "#BC002D", "KOR": "#003478", "LAT": "#9E3039", "LTU": "#FDB913",
    "MEX": "#006847", "MLI": "#14B53A", "NCA": "#0067C6", "NED": "#FF6C00",
    "NGA": "#008751", "NZL": "#1B2432", "PAN": "#005293", "PAR": "#D52B1E",
    "PHI": "#0038A8", "POL": "#DC143C", "PUR": "#ED0000", "SEN": "#00853F",
    "SLO": "#005DA4", "SRB": "#C6363C", "SSD": "#0F47AF", "SVK": "#0B4EA2",
    "SWE": "#006AA7", "TUR": "#E30A17", "URU": "#7BAFD4", "USA": "#0A3161",
    "VEN": "#FCD116", "ANG": "#CE1126",
}


def dsn() -> str:
    return os.environ.get("WAREHOUSE_DSN", DEFAULT_DSN)


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- the five frames the template reads ------------------------------------

GAME_DETAILS_SQL = """
select
    game_id                          as "gameId",
    game_date::text                  as date,
    home_team, home_code             as home_short, home_team_id as home_id, home_score,
    away_team, away_code             as away_short, away_team_id as away_id, away_score,
    country, city, fiba_zone         as "fibaZone",
    competition,
    round_name                       as round,
    group_code                       as "group",
    source_url                       as game_link
from analytics_staging.stg_games
where competition = %s
order by game_date, game_id
"""

# Column names match the original CSV headers exactly — the template's JS reads
# them by those literal keys, percent signs and slashes included. The percent
# signs are doubled because psycopg reads a lone % as a parameter placeholder;
# Postgres receives a single one.
ADV_SQL = """
select
    game_id                                     as "gameId",
    team_name                                   as nationality,
    team_id                                     as "teamId",
    team_code                                   as "shortCode",
    pts                                         as "PTS",
    possessions                                 as "Possessions",
    round(100.0 * pts / nullif(possessions, 0), 1)             as "ORTG",
    round(100.0 * opp_pts / nullif(opp_possessions, 0), 1)     as "DRTG",
    round(100.0 * (fgm + 0.5 * fg3m) / nullif(fga, 0), 1)      as "EFG%%",
    round(100.0 * tov / nullif(possessions, 0), 1)             as "TO/Poss",
    round(100.0 * dreb / nullif(dreb + opp_oreb, 0), 1)        as "DRB rt",
    round(100.0 * ast / nullif(fgm, 0), 1)                     as "AST/FG%%"
from analytics_intermediate.int_team_game_opponent
where competition = %s
"""

# fb_pts and putback_pts are emitted as 0. Both need play-by-play sequence
# logic — a make within N seconds of a defensive rebound, and a make by the
# player who just grabbed an offensive board — that isn't ported yet. Emitting
# zero keeps the two awards that use them inert rather than wrong.
PLAYER_SQL = """
select
    n.game_date::text     as date,
    n.game_id             as "gameId",
    n.player_name         as name,
    n.team_code           as team,
    t.team_name           as nationality,
    n.pts                 as "PTS",
    b.reb                 as "REB",
    b.oreb                as "OR",
    b.dreb                as "DR",
    n.ast                 as "AS",
    n.stl                 as "ST",
    n.blk                 as "BS",
    n.pf                  as "PF",
    n.fouls_drawn         as "FD",
    n.ftm                 as "FTM",
    n.fga                 as "FGA",
    n.fgm                 as "FGM",
    n.fg3m                as "FG3M",
    n.tov                 as "TO",
    m.est_minutes_played  as "MP",
    n.net_points          as "PM",
    b.efficiency          as "EFF",
    n.off_net,
    n.def_net,
    n.stl + n.blk         as stocks,
    case when n.is_starter then 'TRUE' else 'FALSE' end as starter,
    case when o.margin >= 0 then '+' || o.margin::text else o.margin::text end as "WL",
    o.margin              as "WL_raw",
    n.corner3_makes       as corner3m,
    n.abovebreak3_makes   as abovebk3m,
    n.rim_makes,
    n.midrange_makes,
    0                     as fb_pts,
    0                     as putback_pts
from analytics_marts.mart_player_net_points n
join analytics_staging.stg_player_box b
  on b.game_id = n.game_id and b.person_id = n.person_id
join analytics_intermediate.int_team_game_opponent o
  on o.game_id = n.game_id and o.team_code = n.team_code
join (select distinct game_id, team_code, team_name
      from analytics_intermediate.int_team_game_opponent) t
  on t.game_id = n.game_id and t.team_code = n.team_code
left join analytics_intermediate.int_player_minutes m
  on m.game_id = n.game_id and m.person_id = n.person_id
where n.competition = %s
"""

GROUPS_SQL = """
select group_code, array_agg(distinct team_code order by team_code) as teams
from analytics_marts.mart_standings
where competition = %s and group_code is not null
group by group_code
order by group_code
"""

# Extra frames for the sections the original template doesn't have.
COURT_ZONES_SQL = """
select team_code as "shortCode", court_zone as zone, attempts, makes,
       fg_pct as "fg", league_fg_pct as "lgFg", fg_pct_vs_league as "diff",
       share_of_shots as "share"
from analytics_marts.mart_court_zones
where competition = %s
"""

FOUR_FACTORS_SQL = """
select team_code as "shortCode", team_name as nationality, games as gp,
       efg_pct as "efg", opp_efg_pct as "oppEfg",
       tov_pct as "tov", opp_tov_pct as "oppTov",
       oreb_pct as "oreb", dreb_pct as "dreb",
       ft_rate as "ftRate", ortg, drtg, net_rtg as net
from analytics_marts.mart_four_factors
where competition = %s
"""


def build(competition: str, conn_str: str | None = None) -> str:
    with psycopg.connect(conn_str or dsn()) as conn:
        gd = rows(conn, GAME_DETAILS_SQL, (competition,))
        if not gd:
            raise SystemExit(f"No games for {competition!r}")
        adv = rows(conn, ADV_SQL, (competition,))
        players = rows(conn, PLAYER_SQL, (competition,))
        groups = {r["group_code"]: r["teams"] for r in rows(conn, GROUPS_SQL, (competition,))}
        zones = rows(conn, COURT_ZONES_SQL, (competition,))
        factors = rows(conn, FOUR_FACTORS_SQL, (competition,))

    dates = sorted(g["date"] for g in gd if g["date"])
    meta = {
        "name": competition,
        "start": dates[0] if dates else "",
        "end": dates[-1] if dates else "",
        "host": ", ".join(x for x in (gd[0].get("city"), gd[0].get("country")) if x),
    }

    def js(name, obj):
        return f"const {name} = {json.dumps(obj, ensure_ascii=False, default=str)};"

    block = "\n".join([
        START,
        js("GAME_DETAILS", gd),
        js("ADV", adv),
        js("PLAYER_DATA", players),
        # No qualification model yet, so the Qualification zone renders empty
        # rather than showing a made-up cut line.
        js("QUALIFIERS", []),
        js("QUALIFY_SPOTS", 0),
        js("EVENT_META", meta),
        js("GENERATED_AT", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")),
        js("FLAG_MAP", FLAG_MAP),
        js("TEAM_COLORS", TEAM_COLORS),
        js("GROUPS", groups),
        js("COURT_ZONES", zones),
        js("FOUR_FACTORS", factors),
        END,
    ])

    html = TEMPLATE.read_text(encoding="utf-8")
    a, b = html.index(START), html.index(END) + len(END)
    out = html[:a] + block + html[b:]
    log.info("%s: %d games, %d team-games, %d player-games, %d zones",
             competition, len(gd), len(adv), len(players), len(zones))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", required=True)
    ap.add_argument("--out", default="docs/index.html")
    args = ap.parse_args()

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(args.competition), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
