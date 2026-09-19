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
import unicodedata
from datetime import date, datetime
from decimal import Decimal
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

    def encode(v):
        """Decimals must land as JSON numbers, not strings.

        An earlier version used default=str, which shipped off_net as "31.1".
        The template does arithmetic on those fields, so the first renderer hit
        `val.toFixed is not a function`, threw, and took every section after it
        down with it — the whole page below Scores came out blank.
        """
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        raise TypeError(f"Cannot serialise {type(v).__name__}")

    def js(name, obj):
        return f"const {name} = {json.dumps(obj, ensure_ascii=False, default=encode)};"

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


def slugify(name: str) -> str:
    """Short, stable folder name for a competition, same shape as the
    --publish-slug the original scraper takes.

    Accents are folded to ASCII: "Türkiye" in a path becomes a percent-encoded
    URL that is awkward to type and share.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    out = []
    for ch in folded.lower():
        out.append(ch if ch.isalnum() else "-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def competitions(conn_str: str | None = None) -> list[dict]:
    with psycopg.connect(conn_str or dsn()) as conn:
        return rows(conn, """
            select competition,
                   count(*)              as games,
                   min(game_date)::text  as start,
                   max(game_date)::text  as "end",
                   min(city)             as city,
                   min(country)          as country,
                   bool_or(home_code = 'CAN' or away_code = 'CAN') as has_canada
            from analytics_staging.stg_games
            group by competition
            -- A single-game "competition" is a stray fixture rather than a
            -- tournament; the U17 World Cup game seeded for CI shows up that
            -- way and shouldn't get a card on the hub.
            having count(*) > 1
            order by max(game_date) desc
        """)


HUB = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canada Basketball — Tournaments</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap">
<style>
  :root {{ --bg:#f5f6f8; --surface:#fff; --border:#e8eaef; --text:#0f172a;
           --muted:#6b7280; --accent:#D80621; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--bg); color:var(--text);
          font-family:Inter,system-ui,sans-serif; font-size:13.5px; }}
  .hub-head {{ background:var(--accent); color:#fff; padding:26px 24px; display:flex;
               align-items:center; gap:16px; }}
  .hub-head .crest {{ width:56px; height:56px; background:#fff; border-radius:14px;
                      display:grid; place-items:center; font-size:27px; flex:none;
                      box-shadow:0 2px 10px rgba(0,0,0,.24); }}
  .hub-head h1 {{ margin:0; font-size:28px; font-weight:900; letter-spacing:-.8px; }}
  .hub-head .sub {{ font-size:12.5px; opacity:.82; margin-top:3px; font-weight:600; }}
  .wrap {{ max-width:1160px; margin:0 auto; padding:26px 24px 70px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:18px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:14px;
           overflow:hidden; box-shadow:0 1px 3px rgba(16,24,40,.06); display:flex; flex-direction:column; }}
  .card .band {{ height:82px; background:linear-gradient(135deg,var(--accent),#8f1420);
                 display:grid; place-items:center; color:#fff; font-size:34px; position:relative; }}
  .card .badge {{ position:absolute; top:10px; right:10px; background:rgba(0,0,0,.42);
                  border-radius:999px; padding:3px 10px; font-size:10px; font-weight:800;
                  letter-spacing:1.1px; }}
  .card .body {{ padding:15px 17px 17px; display:flex; flex-direction:column; gap:5px; flex:1; }}
  .card h2 {{ margin:0; font-size:15px; font-weight:800; letter-spacing:-.2px; line-height:1.3; }}
  .card .meta {{ font-size:11.5px; color:var(--muted); }}
  .card a {{ margin-top:auto; padding-top:11px; color:var(--accent); font-weight:700;
             font-size:12.5px; text-decoration:none; }}
  .card a:hover {{ text-decoration:underline; }}
  footer {{ text-align:center; color:var(--muted); font-size:11.5px; padding:0 24px 40px; }}
  footer a {{ color:var(--accent); }}
</style></head><body>
<div class="hub-head"><div class="crest">🍁</div>
  <div><h1>Canada Basketball Tournaments</h1>
  <div class="sub">{count} competitions · {games} games · built from the scouting pipeline</div></div></div>
<div class="wrap"><div class="cards">{cards}</div></div>
<footer>Scraped from fiba.basketball, stored raw, transformed with dbt and checked by 82 tests before publishing.<br>
  <a href="https://github.com/jordanngo205/Canada_Basketball_Tournaments_Pipeline">Pipeline source</a></footer>
</body></html>
"""


def build_hub(comps: list[dict]) -> str:
    cards = []
    for c in comps:
        where = ", ".join(x for x in (c["city"], c["country"]) if x)
        when = f'{c["start"][:10]} – {c["end"][:10]}'
        cards.append(
            f'<article class="card"><div class="band">🏀'
            f'<span class="badge">{"CANADA" if c["has_canada"] else "FINAL"}</span></div>'
            f'<div class="body"><h2>{c["competition"]}</h2>'
            f'<div class="meta">{where or "—"}</div>'
            f'<div class="meta">{when} · {c["games"]} games</div>'
            f'<a href="{slugify(c["competition"])}/">View dashboard →</a></div></article>'
        )
    return HUB.format(count=len(comps), games=sum(c["games"] for c in comps),
                      cards="\n".join(cards))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", help="Build one competition. Omit to build them all.")
    ap.add_argument("--docs", default="docs", help="Output root")
    args = ap.parse_args()

    docs = Path(args.docs)
    comps = competitions()
    if args.competition:
        comps = [c for c in comps if c["competition"] == args.competition]
        if not comps:
            raise SystemExit(f"No competition named {args.competition!r}")

    # One folder per tournament, the same layout the published site uses, and a
    # hand-free hub at the root linking to each.
    for c in comps:
        slug = slugify(c["competition"])
        out = docs / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build(c["competition"]), encoding="utf-8")
        print(f"  {slug}/  ({out.stat().st_size // 1024} KB)")

    hub = docs / "index.html"
    hub.write_text(build_hub(competitions()), encoding="utf-8")
    print(f"hub → {hub}")


if __name__ == "__main__":
    main()
