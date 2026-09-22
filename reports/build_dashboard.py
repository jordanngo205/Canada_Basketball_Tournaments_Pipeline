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

# fb_pts and putback_pts come from int_scoring_types, reconstructed from the
# play-by-play because FIBA marks neither. Putbacks are observed; fast-break
# points are a 7-second estimate. They used to ship as literal 0, which left
# the Speed Demon and Cleanup Crew awards permanently blank.
#
# `starter` must be a real boolean, not the string 'TRUE'/'FALSE'. The template
# reads it as `p.starter ? null : p.PM` and aggregates it with
# `a.starter || p.starter`, and in JavaScript the string 'FALSE' is truthy —
# so every player looked like a starter and Spark Plug, the bench award, never
# had a single candidate to rank.
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
    n.is_starter          as starter,
    case when o.margin >= 0 then '+' || o.margin::text else o.margin::text end as "WL",
    o.margin              as "WL_raw",
    n.corner3_makes       as corner3m,
    n.abovebreak3_makes   as abovebk3m,
    n.rim_makes,
    n.midrange_makes,
    n.fb_pts,
    n.putback_pts
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
       efg_pct as "efg", oreb_pct as "oreb", ft_rate as "ftRate", tov_pct as "tov",
       opp_efg_pct as "oppEfg", opp_oreb_pct as "oppOreb",
       opp_ft_rate as "oppFtRate", opp_tov_pct as "oppTov",
       z_efg, z_oreb, z_ft_rate, z_tov,
       z_opp_efg, z_opp_oreb, z_opp_ft_rate, z_opp_tov,
       z_off as "zOff", z_def as "zDef", z_total as "zTotal",
       proj_rank as "projRank", placement,
       extra_poss_pg as "extraPoss",
       ortg, drtg, net_rtg as net
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
        # QUALIFIERS is genuinely unused — renderQualification() never reads
        # it, and the only mention left in the template is a comment. It ships
        # as [] because the template's destructuring list still names it.
        js("QUALIFIERS", []),
        # This one matters. The board is built from GROUPS and the standings,
        # both of which are real; the only thing it needs told is how many
        # teams advance. Shipping 0 here was not "no cut line" — the template
        # reads a falsy value as "use the default of 2", so the U17 World Cup
        # (4 advance) and WC Qualifying Türkiye (3) were both drawing the line
        # in the wrong place while looking perfectly plausible.
        js("QUALIFY_SPOTS", QUALIFY_SPOTS.get(slugify(competition), DEFAULT_QUALIFY_SPOTS)),
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


# The published site uses short hand-picked slugs, and the banner images are
# named after them. Matching those keeps the folder layout, the URLs and the
# artwork all lined up; anything new falls back to a generated slug and simply
# renders without a banner.
PUBLISHED_SLUGS = {
    "FIBA Women's Basketball World Cup 2026 Qualifying Tournament Türkiye": "wc-qualifying-istanbul-2026",
    "FIBA U18 Women's AmeriCup": "u18-americup-2026",
    "FIBA U17 Women's Basketball World Cup": "u17-world-cup-2026",
    "FIBA Women's Olympic Pre-Qualifying Tournament": "olympic-pre-qualifying-2026",
}


# How many teams advance from each group — the cut line on the Qualification
# board. Ported from the --qualify-spots flag on the corresponding workflow in
# the Canada-Basketball-Tournaments repo, which is the only place these were
# ever written down. They are a property of the tournament's own format, not
# anything derivable from the box scores, so they have to be stated somewhere.
#
# A tournament that isn't listed falls back to the template's own default of 2,
# which is the common case but wrong for a four-group World Cup.
QUALIFY_SPOTS = {
    "olympic-pre-qualifying-2026": 2,
    "u17-world-cup-2026": 4,
    "u18-americup-2026": 2,
    "wc-qualifying-istanbul-2026": 3,
}

DEFAULT_QUALIFY_SPOTS = 2


def slugify(name: str) -> str:
    """Short, stable folder name for a competition, same shape as the
    --publish-slug the original scraper takes.

    Accents are folded to ASCII: "Türkiye" in a path becomes a percent-encoded
    URL that is awkward to type and share.
    """
    if name in PUBLISHED_SLUGS:
        return PUBLISHED_SLUGS[name]
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
            -- No `having count(*) > 1` here any more. That used to filter out
            -- CI's seeded fixture, which arrived as a one-game "competition",
            -- but it was a guess standing in for provenance and it would have
            -- hidden a real tournament on its opening day just as effectively.
            -- Fixture rows are flagged at the raw layer now and dropped by the
            -- latest_games() macro, so everything reaching stg_games is real.
            order by max(game_date) desc
        """)


HUB_TEMPLATE = Path(__file__).parent / "template" / "hub_template.html"

# Display names and dates on the published hub are written by hand and read
# better than the raw competition strings, so they're kept. Anything not listed
# falls back to what the warehouse holds.
HUB_LABELS = {
    "wc-qualifying-istanbul-2026": (
        "FIBA Women's Basketball World Cup 2026 Qualifying Tournament — Istanbul",
        "Istanbul, Türkiye", "11–17 Mar 2026"),
    "u18-americup-2026": (
        "FIBA U18 Women's AmeriCup 2026", "Irapuato, Mexico", "9–15 Jun 2026"),
    "u17-world-cup-2026": (
        "FIBA U17 Women's Basketball World Cup 2026", "Brno, Czechia", "11–19 Jul 2026"),
    "olympic-pre-qualifying-2026": (
        "FIBA Women's Olympic Pre-Qualifying Tournament 2026", "Guadalajara, Mexico", "17–23 Aug 2026"),
}


def _card(comp: dict) -> str:
    """One card, in the published hub's own markup.

    A tournament without artwork gets a plain red panel with the crest rather
    than a broken image — the four published events have banners, anything new
    will not until someone draws one.
    """
    slug = slugify(comp["competition"])
    # Banner presence is decided by the label table, not by looking on disk:
    # the builder runs in a container where docs/ isn't mounted, so a file
    # check there silently reported every banner missing.
    has_banner = slug in HUB_LABELS
    label, place, when = HUB_LABELS.get(slug, (
        comp["competition"],
        ", ".join(x for x in (comp["city"], comp["country"]) if x) or "—",
        f'{comp["start"][:10]} – {comp["end"][:10]}',
    ))
    art = (f'<img src="./assets/banners/{slug}.webp" alt="{label}" loading="lazy">'
           if has_banner else
           '<div class="banner-fallback">🍁</div>')
    return f'''
      <a class="card" href="./{slug}/">
        <div class="banner">
          {art}
          <span class="badge">Final</span>
        </div>
        <div class="body">
          <p class="full-name">{label}</p>
          <div class="meta">{place} &nbsp;·&nbsp; {when}</div>
          <div class="cta">View dashboard &rarr;</div>
        </div>
      </a>'''


def build_hub(comps: list[dict]) -> str:
    html = HUB_TEMPLATE.read_text(encoding="utf-8")
    cards = "\n".join(_card(c) for c in comps)
    # Swap everything between the grid tags for freshly generated cards, so the
    # hub tracks whatever the warehouse holds instead of being edited by hand.
    a = html.index('<div class="grid">') + len('<div class="grid">')
    b = html.index("</div>\n  </main>")
    out = html[:a] + "\n" + cards + "\n    " + html[b:]
    # A fallback panel for events without artwork.
    return out.replace("</style>", '''  .banner-fallback {
    width: 100%; height: 100%; display: grid; place-items: center;
    background: linear-gradient(135deg, var(--accent), #8f1420); font-size: 40px;
  }
</style>''')


def build_all(docs: Path, competition: str | None = None) -> list[str]:
    """Write every tournament page plus the hub. Returns the slugs written.

    Split out of main() so the scheduled runner can call it directly instead of
    shelling out to this module — one less subprocess whose failure has to be
    inferred from an exit code.
    """
    comps = competitions()
    wanted = comps
    if competition:
        wanted = [c for c in comps if c["competition"] == competition]
        if not wanted:
            raise SystemExit(f"No competition named {competition!r}")

    # One folder per tournament, the same layout the published site uses, and a
    # hand-free hub at the root linking to each.
    written = []
    for c in wanted:
        slug = slugify(c["competition"])
        out = docs / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build(c["competition"]), encoding="utf-8")
        print(f"  {slug}/  ({out.stat().st_size // 1024} KB)")
        written.append(slug)

    # The hub always lists every competition, not just the one rebuilt, so a
    # single-competition run cannot silently drop the others off the homepage.
    hub = docs / "index.html"
    hub.write_text(build_hub(comps), encoding="utf-8")
    print(f"hub → {hub}")
    return written


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", help="Build one competition. Omit to build them all.")
    ap.add_argument("--docs", default="docs", help="Output root")
    args = ap.parse_args()
    build_all(Path(args.docs), args.competition)


if __name__ == "__main__":
    main()
