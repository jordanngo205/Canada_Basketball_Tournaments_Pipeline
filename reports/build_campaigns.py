"""Comparison pages for qualifying campaigns.

A campaign is a set of tournaments played in parallel at different venues that
feed the same final event: the four World Cup 2026 Qualifying Tournaments, for
example. Canada only ever plays in one of them, so its own dashboard can rank
Canada against five teams. This page ranks it against all of them.

Campaigns are defined in config/events.yml. Each one matches competitions by
the start of FIBA's competition name, and is only built when at least two
venues are in the warehouse — a one-tournament campaign has nothing to compare.

Nothing here is a new metric. Team ratings come from the same per-game rows the
tournament dashboards use (int_team_game_opponent), and player numbers from
mart_player_leaders. The one addition is opponent strength, which matters more
here than anywhere else: a +20 net rating in a weak group and a +20 in a strong
one are not the same result, and the venues were not drawn to be equal.
"""

from __future__ import annotations

import html
import json
import logging
from decimal import Decimal
from pathlib import Path

import psycopg

from reports.build_dashboard import FLAG_MAP, dsn, rows, slugify

log = logging.getLogger(__name__)

# Per-team ratings across every game the team played in its own tournament.
#
# Rates, never totals: venues played different numbers of games (round robin
# only, or with knockouts), so totals would just reward the teams that played
# the most. Everything is per 100 possessions or a percentage.
#
# opp_net is the average net rating of the opponents a team actually faced,
# each opponent's rating taken from its whole tournament. adj_net adds it to the
# team's own net rating — the first step of a simple rating system: beating
# teams that are good elsewhere counts for more than beating teams that aren't.
TEAMS_SQL = """
with games as (
    select * from analytics_intermediate.int_team_game_opponent
    where competition like %s
),
team as (
    select
        competition, team_code, max(team_name) as team_name,
        count(*)  as gp,
        sum(win)  as w,
        sum(loss) as l,
        round(sum(pts)::numeric / count(*), 1)                          as ppg,
        round(sum(opp_pts)::numeric / count(*), 1)                      as opp_ppg,
        round(100.0 * sum(pts) / nullif(sum(possessions), 0), 1)        as ortg,
        round(100.0 * sum(opp_pts) / nullif(sum(opp_possessions), 0), 1) as drtg,
        round(100.0 * (sum(fgm) + 0.5 * sum(fg3m)) / nullif(sum(fga), 0), 1) as efg,
        round(100.0 * sum(tov) / nullif(sum(possessions), 0), 1)        as tov,
        round(100.0 * sum(oreb) / nullif(sum(oreb) + sum(opp_dreb), 0), 1) as oreb,
        round(100.0 * sum(fta) / nullif(sum(fga), 0), 1)                as ftr,
        -- The same four factors from the other side of the ball: what the
        -- team let its opponents do. DREB%% is the defensive twin of OREB%%.
        round(100.0 * (sum(opp_fgm) + 0.5 * sum(opp_fg3m)) / nullif(sum(opp_fga), 0), 1) as opp_efg,
        round(100.0 * sum(opp_tov) / nullif(sum(opp_possessions), 0), 1) as opp_tov,
        round(100.0 * sum(dreb) / nullif(sum(dreb) + sum(opp_oreb), 0), 1) as dreb,
        round(100.0 * sum(opp_fta) / nullif(sum(opp_fga), 0), 1)        as opp_ftr,
        -- Extra shots a team earns per game: its offensive rebounds and the
        -- turnovers it forces, minus the same given up. The four factors'
        -- possession side in one number.
        round((sum(oreb) + sum(opp_tov) - sum(opp_oreb) - sum(tov))::numeric / count(*), 1) as extra_poss,
        sum(possessions) as poss
    from games
    group by competition, team_code
),
rated as (
    select *, round(ortg - drtg, 1) as net from team
),
schedule as (
    select g.competition, g.team_code, round(avg(o.net), 1) as opp_net
    from games g
    join rated o on o.competition = g.competition and o.team_code = g.opp_team_code
    group by g.competition, g.team_code
)
select r.*, s.opp_net, round(r.net + s.opp_net, 1) as adj_net, p.placement
from rated r
join schedule s using (competition, team_code)
left join analytics_marts.mart_final_placement p
  on p.competition = r.competition and p.team_code = r.team_code
order by adj_net desc
"""

# Per-game player numbers, three-game minimum so a single hot night in a
# blowout doesn't top the board.
PLAYERS_SQL = """
select competition, player_name, team_code, games_played as gp,
       ppg, rpg, apg, spg, bpg, eff_per_game as eff,
       round(ts_pct, 1) as ts,
       -- Ten attempts before a three-point percentage counts: 2-for-2 is not a shooter.
       case when fg3a >= 10 then round(fg3_pct, 1) end as fg3,
       fg3a
from analytics_marts.mart_player_leaders
where competition like %s and games_played >= 3
"""

VENUES_SQL = """
select competition, min(city) as city, min(country) as country,
       min(game_date)::text as start, max(game_date)::text as "end",
       count(*) as games,
       bool_or(home_code = 'CAN' or away_code = 'CAN') as has_canada
from analytics_staging.stg_games
where competition like %s
group by competition
order by bool_or(home_code = 'CAN' or away_code = 'CAN') desc, min(city)
"""


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def campaign_data(match: str, conn_str: str | None = None) -> dict:
    pattern = match.replace("%", r"\%") + "%"
    with psycopg.connect(conn_str or dsn()) as conn:
        venues = rows(conn, VENUES_SQL, (pattern,))
        teams = rows(conn, TEAMS_SQL, (pattern,))
        players = rows(conn, PLAYERS_SQL, (pattern,))
    clean = lambda rs: [{k: _num(v) for k, v in r.items()} for r in rs]
    return {"venues": clean(venues), "teams": clean(teams), "players": clean(players)}


def _venue_label(v: dict) -> str:
    """The venue's city, which is how people refer to these ("Istanbul"), not
    FIBA's competition name, which only carries the country."""
    return v.get("city") or v["competition"].rsplit(" ", 1)[-1]


def _date_range(start: str, end: str) -> str:
    """'2026-03-11', '2026-03-17' -> '11–17 Mar 2026', the way the hub writes it."""
    from datetime import date
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b:%b %Y}"
    return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"


# FIBA's long country names, shortened for the venue cards.
SHORT_COUNTRY = {"People 's Republic of China": "China", "People's Republic of China": "China"}


def build_campaign(campaign: dict, data: dict) -> str:
    venues = data["venues"]
    label_of = {v["competition"]: _venue_label(v) for v in venues}
    for t in data["teams"]:
        # FIBA's own name for China carries a stray space: "People 's Republic".
        t["team_name"] = (t["team_name"] or "").replace(" 's", "'s")
        t["venue"] = label_of.get(t["competition"], "")
        t["slug"] = slugify(t["competition"])
        t["iso"] = FLAG_MAP.get(t["team_code"], "")
    for p in data["players"]:
        p["venue"] = label_of.get(p["competition"], "")
        p["iso"] = FLAG_MAP.get(p["team_code"], "")

    venue_cards = []
    for v in venues:
        standings = sorted(
            (t for t in data["teams"] if t["competition"] == v["competition"]),
            key=lambda t: (t["placement"] if t["placement"] is not None else 99, -t["w"], -t["net"]),
        )
        lines = "".join(
            f'<li class="{"can" if t["team_code"] == "CAN" else ""}">'
            f'<span class="fi fi-{t["iso"]}"></span>{html.escape(t["team_code"])}'
            f'<span class="rec">{t["w"]}–{t["l"]}</span></li>'
            for t in standings
        )
        tag = '<span class="tag">Canada</span>' if v["has_canada"] else ""
        venue_cards.append(f'''
      <a class="venue" href="../{slugify(v["competition"])}/">
        <div class="venue-head"><strong>{html.escape(_venue_label(v))}</strong>{tag}</div>
        <div class="venue-meta">{html.escape(SHORT_COUNTRY.get(v["country"], v["country"] or ""))} · {_date_range(v["start"], v["end"])} · {v["games"]} games</div>
        <ol>{lines}</ol>
        <div class="cta">Full dashboard &rarr;</div>
      </a>''')

    payload = json.dumps(data, ensure_ascii=False)
    n_teams = len(data["teams"])
    n_games = sum(v["games"] for v in venues)
    title = html.escape(campaign["name"])

    return PAGE.replace("%%TITLE%%", title) \
        .replace("%%SHORT%%", html.escape(campaign["short"])) \
        .replace("%%SUB%%", f"{len(venues)} tournaments · {n_teams} teams · {n_games} games") \
        .replace("%%VENUES%%", "".join(venue_cards)) \
        .replace("%%DATA%%", payload)


def eligible_campaigns(campaigns: list[dict], comps: list[dict]) -> list[dict]:
    """Campaigns with at least two venues in the warehouse."""
    out = []
    for c in campaigns or []:
        members = [x for x in comps if x["competition"].startswith(c["match"])]
        if len(members) >= 2:
            out.append({**c, "members": members})
        else:
            log.info("campaign %s: %d tournament(s) — skipped, nothing to compare",
                     c["slug"], len(members))
    return out


def build_all_campaigns(docs: Path, campaigns: list[dict]) -> list[str]:
    written = []
    for c in campaigns:
        data = campaign_data(c["match"])
        out = docs / c["slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_campaign(c, data), encoding="utf-8")
        print(f"  {c['slug']}/  (comparison, {len(data['teams'])} teams)")
        written.append(c["slug"])
    return written


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%%SHORT%% — Compare</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/css/flag-icons.min.css"/>
<style>
  :root {
    --bg: #f5f6f8; --surface: #fff; --border: #e8eaef; --border2: #d3d8e0;
    --text: #0f172a; --muted: #6b7280; --accent: #D80621; --can: #fff1f2;
    --good: #15803d; --bad: #b91c1c;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: 'Inter', system-ui, sans-serif; line-height: 1.45; }
  .header { background: var(--accent); color: #fff; padding: 22px 20px; }
  .header-inner { max-width: 1240px; margin: 0 auto; }
  .back { color: #fff; opacity: .85; font-size: 13px; text-decoration: none; font-weight: 600; }
  .back:hover { opacity: 1; }
  .header h1 { margin: 8px 0 4px; font-size: 24px; font-weight: 800; }
  .header p { margin: 0; font-size: 14px; opacity: .9; }
  main { max-width: 1240px; margin: 0 auto; padding: 24px 20px 60px; }
  h2 { font-size: 13px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase;
       color: var(--muted); margin: 32px 0 12px; display: flex; gap: 12px; align-items: center; }
  h2::after { content: ""; flex: 1; height: 1px; background: var(--border2); }
  h2:first-child { margin-top: 0; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
          box-shadow: 0 1px 3px rgba(0,0,0,.05); }

  /* Canada at a glance */
  .glance { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 12px; }
  .stat { padding: 14px 16px; }
  .stat .k { font-size: 12px; color: var(--muted); font-weight: 600; }
  .stat .v { font-size: 26px; font-weight: 800; margin-top: 2px; }
  .stat .s { font-size: 12px; color: var(--muted); }
  @media (max-width: 900px) { .glance { grid-template-columns: repeat(2, minmax(0,1fr)); } }

  /* Venues */
  .venues { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; }
  @media (max-width: 1000px) { .venues { grid-template-columns: repeat(2, minmax(0,1fr)); } }
  @media (max-width: 560px)  { .venues { grid-template-columns: 1fr; } }
  .venue { display: block; background: var(--surface); border: 1px solid var(--border);
           border-radius: 14px; padding: 14px 16px; color: inherit; text-decoration: none;
           transition: transform .12s, box-shadow .12s; }
  .venue:hover { transform: translateY(-2px); box-shadow: 0 8px 18px rgba(0,0,0,.08); }
  .venue-head { display: flex; justify-content: space-between; align-items: center; font-size: 15px; }
  .venue-meta { font-size: 12px; color: var(--muted); margin: 2px 0 8px; }
  .tag { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em;
         color: #fff; background: var(--accent); border-radius: 999px; padding: 2px 8px; }
  .venue ol { margin: 0; padding-left: 20px; font-size: 13px; }
  .venue li { padding: 2px 0; }
  .venue li .fi { margin-right: 6px; }
  .venue li .rec { float: right; color: var(--muted); font-variant-numeric: tabular-nums; }
  .venue li.can { font-weight: 800; color: var(--accent); }
  .venue .cta { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border);
                font-size: 12.5px; font-weight: 700; color: var(--accent); }

  /* Tables */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { padding: 8px 10px; text-align: right; white-space: nowrap; }
  th { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
       border-bottom: 1px solid var(--border2); cursor: pointer; user-select: none; background: var(--surface);
       position: sticky; top: 0; }
  th:hover { color: var(--text); }
  th.sorted { color: var(--accent); }
  th .arr { font-size: 9px; margin-left: 3px; }
  td { border-bottom: 1px solid var(--border); }
  th.l, td.l { text-align: left; }
  tr.can td { background: var(--can); font-weight: 700; }
  tr.can td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
  td .fi { margin-right: 8px; border-radius: 2px; }
  td.rank { color: var(--muted); width: 1%; }
  .pos { color: var(--good); } .neg { color: var(--bad); }
  .note { font-size: 12.5px; color: var(--muted); margin: 10px 2px 0; max-width: 900px; }
  .note b { color: var(--text); }

  .more { display: block; margin: 8px auto 0; border: 1px solid var(--border2); background: var(--surface);
          border-radius: 999px; padding: 6px 16px; font: inherit; font-size: 12.5px; font-weight: 700;
          color: var(--accent); cursor: pointer; }
  .more:hover { border-color: var(--accent); }
  tr.gap td { padding: 2px; text-align: center; color: var(--muted2, #9ca3af); border-bottom: 1px dashed var(--border2); }
  .ff { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }
  @media (max-width: 800px) { .ff { grid-template-columns: 1fr; } }
  .ff-side { padding: 14px 16px; }
  .ff-side h3 { margin: 0 0 10px; font-size: 14px; }
  .ff-row { display: grid; grid-template-columns: 150px 60px 1fr 64px; align-items: center; gap: 10px;
            font-size: 13px; padding: 5px 0; }
  .ff-row .val { font-weight: 800; text-align: right; font-variant-numeric: tabular-nums; }
  .ff-bar { height: 8px; background: var(--border); border-radius: 99px; position: relative; }
  .ff-bar span { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 99px; }
  .ff-row .rk { font-size: 12px; color: var(--muted); text-align: right; }
  .top { background: var(--good); } .mid { background: #9ca3af; } .bot { background: var(--bad); }
  .scatter-card { padding: 14px 16px 8px; }
  .scatter-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
                  font-size: 12.5px; color: var(--muted); margin-bottom: 4px; }
  .key { display: inline-flex; align-items: center; gap: 6px; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #94a3b8; margin-left: 8px; }
  .dot.can { background: var(--accent); }
  #scatter svg { width: 100%; height: auto; display: block; overflow: visible; }
  #scatter .grid line { stroke: var(--border); }
  #scatter .axis text { fill: var(--muted); font-size: 11px; }
  #scatter .avg { stroke: var(--border2); stroke-dasharray: 4 4; }
  #scatter .quad { fill: var(--muted); font-size: 11px; font-weight: 600; opacity: .8; }
  .scatter-card svg .flagbox { fill: none; stroke: rgba(15,23,42,.18); stroke-width: 1; }
  .scatter-card svg .flagbox.can { stroke: var(--accent); stroke-width: 2.5; }
  .scatter-card svg .hit { fill: transparent; cursor: pointer; }
  .scatter-card svg .lbl { font-size: 11px; font-weight: 700; fill: var(--text); }
  .scatter-card svg .lbl.can { fill: var(--accent); font-size: 12.5px; }
  .scatter-card svg .grid line { stroke: var(--border); }
  .scatter-card svg .axis text { fill: var(--muted); font-size: 11px; }
  .scatter-card svg .avg { stroke: var(--border2); stroke-dasharray: 4 4; }
  .scatter-card svg .quad { fill: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .04em; opacity: .85; }
  .scatter-card svg { width: 100%; height: auto; display: block; overflow: visible; }
  .tip { position: fixed; pointer-events: none; background: var(--text); color: #fff; font-size: 12px;
         padding: 7px 10px; border-radius: 8px; line-height: 1.4; display: none; z-index: 10;
         box-shadow: 0 6px 16px rgba(0,0,0,.2); }
  .tip b { font-size: 13px; }
  .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
  .tab { border: 1px solid var(--border2); background: var(--surface); border-radius: 999px;
         padding: 5px 12px; font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer; color: var(--muted); }
  .tab.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .filter { font-size: 12.5px; color: var(--muted); margin-left: auto; display: flex; align-items: center; gap: 6px; }
</style>
</head>
<body>
  <div class="header"><div class="header-inner">
    <a class="back" href="../">&larr; All tournaments</a>
    <h1>%%TITLE%%</h1>
    <p>%%SUB%% · where Canada stands across every venue</p>
  </div></div>
  <main>
    <h2>Canada at a glance</h2>
    <div class="glance" id="glance"></div>

    <h2>The tournaments</h2>
    <div class="venues">%%VENUES%%</div>

    <h2>Every team</h2>
    <div class="card table-wrap"><table id="teams"></table></div>
    <button class="more" id="teams-more"></button>

    <div class="card scatter-card" style="margin-top:14px">
      <div id="scatter"></div>
    </div>

    <h2>Four factors</h2>
    <div class="ff" id="ff"></div>
    <div class="card scatter-card" style="margin-top:14px">
      <div class="scatter-head">Extra possessions vs shooting. Across: offensive rebounds plus turnovers forced, less the same given up, per game — how many more shots a team gets than its opponent. Up: how well it makes them (eFG%).</div>
      <div id="scatter-ff"></div>
    </div>
    <div class="card table-wrap" style="margin-top:14px"><table id="factors"></table></div>
    <button class="more" id="factors-more"></button>

    <h2>Player leaders</h2>
    <div class="tabs" id="ptabs"></div>
    <div class="card table-wrap"><table id="players"></table></div>
    <button class="more" id="players-more"></button>
    <p class="note">Per game, minimum 3 games played. Canada's players are highlighted.</p>
  </main>

<script>
const DATA = %%DATA%%;
const CAN = 'CAN';
const flag = iso => iso ? `<span class="fi fi-${iso}"></span>` : '';
const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d);
const signed = v => v == null ? '—' : `<span class="${v > 0 ? 'pos' : v < 0 ? 'neg' : ''}">${v > 0 ? '+' : ''}${fmt(v)}</span>`;

function rankOf(rows, key, higherIsBetter = true) {
  const sorted = [...rows].sort((a, b) => higherIsBetter ? b[key] - a[key] : a[key] - b[key]);
  return sorted.findIndex(r => r.team_code === CAN) + 1;
}
const ord = n => n + (['th','st','nd','rd'][((n % 100) - 20) % 10] || ['th','st','nd','rd'][n % 100] || 'th');

// Canada at a glance
(() => {
  const T = DATA.teams, can = T.find(t => t.team_code === CAN);
  if (!can) { document.getElementById('glance').innerHTML = '<p class="note">Canada did not play in these tournaments.</p>'; return; }
  const n = T.length;
  const tiles = [
    ['Adjusted net rating', signed(can.adj_net), `${ord(rankOf(T, 'adj_net'))} of ${n}`],
    ['Net rating', signed(can.net), `${ord(rankOf(T, 'net'))} of ${n}`],
    ['Offence (ORtg)', fmt(can.ortg), `${ord(rankOf(T, 'ortg'))} of ${n}`],
    ['Defence (DRtg)', fmt(can.drtg), `${ord(rankOf(T, 'drtg', false))} of ${n}`],
    ['Schedule strength', signed(can.opp_net), `${ord(rankOf(T, 'opp_net'))} hardest of ${n}`],
  ];
  document.getElementById('glance').innerHTML = tiles.map(([k, v, s]) =>
    `<div class="card stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
})();

// Team scatter with flags for marks. Used twice: offence vs defence, and
// extra possessions vs shooting. Canada's flag is larger with a red outline;
// Canada and the three best teams on the y metric are labelled, the rest are
// on hover so 24 labels don't pile up.
const FLAG_URL = iso => `https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/flags/4x3/${iso}.svg`;
const tipEl = Object.assign(document.createElement('div'), { className: 'tip' });
document.body.appendChild(tipEl);
function teamScatter(el, o) {
  const T = DATA.teams, W = 1100, H = 440, m = { l: 56, r: 30, t: 22, b: 42 };
  const xs = T.map(t => t[o.x]), ys = T.map(t => t[o.y]);
  const px = o.xPad, py = o.yPad;
  const x0 = Math.min(...xs) - px, x1 = Math.max(...xs) + px;
  const y0 = Math.min(...ys) - py, y1 = Math.max(...ys) + py;
  const X = v => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r);
  // "better" is always up: invertY puts low values (e.g. DRtg) at the top.
  const Y = v => o.invertY ? m.t + (v - y0) / (y1 - y0) * (H - m.t - m.b)
                           : H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b);
  const avg = a => a.reduce((s, v) => s + v, 0) / a.length;
  const ax = avg(xs), ay = avg(ys);
  const ticks = (a, b, st) => { const out = []; for (let v = Math.ceil(a / st) * st; v <= b; v += st) out.push(+v.toFixed(4)); return out; };
  const best = [...T].sort((a, b) => o.invertY ? a[o.y] - b[o.y] : b[o.y] - a[o.y]).slice(0, 3).map(t => t.team_code);
  const labelled = new Set([CAN, ...best]);
  const order = [...T].sort((a, b) => (a.team_code === CAN) - (b.team_code === CAN));  // Canada drawn last, on top
  const flagMark = t => {
    const can = t.team_code === CAN, w = can ? 30 : 22, h = w * 3 / 4;
    const cx = X(t[o.x]), cy = Y(t[o.y]);
    const lab = labelled.has(t.team_code)
      ? `<text class="lbl ${can ? 'can' : ''}" x="${cx + w / 2 + 6}" y="${cy + 4}">${t.team_code}</text>` : '';
    return `<image href="${FLAG_URL(t.iso)}" x="${cx - w / 2}" y="${cy - h / 2}" width="${w}" height="${h}" preserveAspectRatio="none"/>
      <rect class="flagbox ${can ? 'can' : ''}" x="${cx - w / 2}" y="${cy - h / 2}" width="${w}" height="${h}" rx="2"/>${lab}`;
  };
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${o.aria}">
    <g class="grid">${ticks(x0, x1, o.xStep).map(v => `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${H - m.b}"/>`).join('')}
      ${ticks(y0, y1, o.yStep).map(v => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/>`).join('')}</g>
    <line class="avg" x1="${X(ax)}" x2="${X(ax)}" y1="${m.t}" y2="${H - m.b}"/>
    <line class="avg" x1="${m.l}" x2="${W - m.r}" y1="${Y(ay)}" y2="${Y(ay)}"/>
    <text class="quad" x="${W - m.r - 4}" y="${m.t - 6}" text-anchor="end">${o.good}</text>
    <text class="quad" x="${m.l + 6}" y="${H - m.b - 8}">${o.bad}</text>
    <g class="axis">${ticks(x0, x1, o.xStep).map(v => `<text x="${X(v)}" y="${H - m.b + 18}" text-anchor="middle">${o.xFmt(v)}</text>`).join('')}
      ${ticks(y0, y1, o.yStep).map(v => `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${o.yFmt(v)}</text>`).join('')}
      <text x="${(W + m.l) / 2}" y="${H - 4}" text-anchor="middle">${o.xLabel}</text>
      <text transform="translate(14 ${(H - m.b + m.t) / 2}) rotate(-90)" text-anchor="middle">${o.yLabel}</text></g>
    ${order.map(flagMark).join('')}
    ${T.map((t, i) => `<rect class="hit" data-i="${i}" x="${X(t[o.x]) - 16}" y="${Y(t[o.y]) - 13}" width="32" height="26"/>`).join('')}
  </svg>`;
  el.querySelectorAll('.hit').forEach(r => {
    r.onmousemove = e => {
      const t = T[r.dataset.i];
      tipEl.innerHTML = `<b>${t.team_name}</b> · ${t.venue} · ${t.w}–${t.l}<br>${o.tip(t)}`;
      tipEl.style.display = 'block'; tipEl.style.left = (e.clientX + 14) + 'px'; tipEl.style.top = (e.clientY + 14) + 'px';
    };
    r.onmouseleave = () => { tipEl.style.display = 'none'; };
  });
}

teamScatter(document.getElementById('scatter'), {
  x: 'ortg', y: 'drtg', invertY: true, xPad: 4, yPad: 4, xStep: 10, yStep: 10,
  xFmt: v => v, yFmt: v => v,
  xLabel: 'Offensive rating — points scored per 100 possessions →',
  yLabel: 'Defensive rating — allowed per 100 (better at top)',
  good: 'GOOD OFFENCE · GOOD DEFENCE', bad: 'STRUGGLING ON BOTH',
  aria: 'Offensive rating against defensive rating for every team',
  tip: t => `ORtg ${fmt(t.ortg)} · DRtg ${fmt(t.drtg)} · Net ${t.net > 0 ? '+' : ''}${fmt(t.net)}`,
});

// Sortable table helper
const LIMIT = 10;
function sortable(el, cols, data, initial, moreBtn) {
  let key = initial.key, desc = initial.desc, open = false;
  if (moreBtn) moreBtn.onclick = () => { open = !open; render(); };
  function render() {
    const col = cols.find(c => c.key === key);
    const rows = [...data].sort((a, b) => {
      const x = a[key], y = b[key];
      if (x == null) return 1; if (y == null) return -1;
      if (typeof x === 'string') return desc ? y.localeCompare(x) : x.localeCompare(y);
      return desc ? y - x : x - y;
    });
    // Collapsed: the top ten, plus Canada's rows wherever they rank, so the
    // comparison the page exists for never needs a click.
    const ranked = rows.map((r, i) => [r, i + 1]);
    let shown = ranked;
    if (!open && rows.length > LIMIT) {
      // At most three pinned Canada rows, or the player table pins the
      // whole roster and stops being short.
      const pinned = ranked.filter(([r, n]) => n > LIMIT && r.team_code === CAN).slice(0, 3);
      shown = ranked.filter(([r, n]) => n <= LIMIT).concat(pinned);
    }
    const line = ([r, n]) => `<tr class="${r.team_code === CAN ? 'can' : ''}"><td class="rank">${n}</td>${cols.map(c =>
        `<td class="${c.l ? 'l' : ''}">${c.fmt ? c.fmt(r[c.key], r) : (r[c.key] ?? '—')}</td>`).join('')}</tr>`;
    let body = '', prev = 0;
    for (const item of shown) {
      if (item[1] > prev + 1) body += `<tr class="gap"><td colspan="${cols.length + 1}">⋯</td></tr>`;
      body += line(item); prev = item[1];
    }
    el.innerHTML = `<thead><tr><th class="rank">#</th>${cols.map(c =>
      `<th class="${c.l ? 'l' : ''} ${c.key === key ? 'sorted' : ''}" data-k="${c.key}" title="${c.tip || ''}">${c.label}${c.key === key ? `<span class="arr">${desc ? '▼' : '▲'}</span>` : ''}</th>`).join('')}</tr></thead>
      <tbody>${body}</tbody>`;
    if (moreBtn) {
      moreBtn.style.display = rows.length > LIMIT ? '' : 'none';
      moreBtn.textContent = open ? 'Show fewer ▴' : `Show all ${rows.length} ▾`;
    }
    el.querySelectorAll('th[data-k]').forEach(th => th.onclick = () => {
      const k = th.dataset.k, c = cols.find(c => c.key === k);
      if (k === key) desc = !desc; else { key = k; desc = c.lowerBetter ? false : !c.l; }
      render();
    });
  }
  render();
  return { set(k, d) { key = k; desc = d; render(); } };
}

// Every team
sortable(document.getElementById('teams'), [
  { key: 'team_name', label: 'Team', l: true, fmt: (v, r) => `${flag(r.iso)}${v}` },
  { key: 'venue', label: 'Venue', l: true, fmt: (v, r) => `<a href="../${r.slug}/" style="color:inherit">${v}</a>` },
  { key: 'w', label: 'W–L', fmt: (v, r) => `${r.w}–${r.l}` },
  { key: 'adj_net', label: 'Adj Net', fmt: signed, tip: 'Net rating + strength of schedule' },
  { key: 'net', label: 'Net', fmt: signed, tip: 'Points per 100 possessions, scored minus allowed' },
  { key: 'opp_net', label: 'Opp Net', fmt: signed, tip: 'Average net rating of opponents faced' },
  { key: 'ortg', label: 'ORtg', fmt: v => fmt(v), tip: 'Points scored per 100 possessions' },
  { key: 'drtg', label: 'DRtg', fmt: v => fmt(v), lowerBetter: true, tip: 'Points allowed per 100 possessions (lower is better)' },
  { key: 'ppg', label: 'PPG', fmt: v => fmt(v) },
  { key: 'opp_ppg', label: 'Opp PPG', fmt: v => fmt(v), lowerBetter: true },
], DATA.teams, { key: 'adj_net', desc: true }, document.getElementById('teams-more'));

// Four factors: Canada's offence and defence ranked against every team, then
// the full table underneath.
const FACTORS = [
  // key, label, higher is better, side
  ['efg', 'Shooting (eFG%)', true, 'off'], ['tov', 'Turnovers (TOV%)', false, 'off'],
  ['oreb', 'Off. rebounding (OREB%)', true, 'off'], ['ftr', 'Free throws (FT rate)', true, 'off'],
  ['opp_efg', 'Opp. shooting (eFG%)', false, 'def'], ['opp_tov', 'Forced turnovers (TOV%)', true, 'def'],
  ['dreb', 'Def. rebounding (DREB%)', true, 'def'], ['opp_ftr', 'Opp. free throws (FT rate)', false, 'def'],
];
(() => {
  const T = DATA.teams, can = T.find(t => t.team_code === CAN), n = T.length;
  if (!can) return;
  const side = (s, title) => `<div class="card ff-side"><h3>${title}</h3>${FACTORS.filter(f => f[3] === s).map(([k, l, hib]) => {
    const r = rankOf(T, k, hib), cls = r <= n / 3 ? 'top' : r > 2 * n / 3 ? 'bot' : 'mid';
    const w = Math.round(100 * (n - r + 1) / n);
    return `<div class="ff-row"><span>${l}</span><span class="val">${fmt(can[k])}</span>
      <div class="ff-bar"><span class="${cls}" style="width:${w}%"></span></div><span class="rk">${ord(r)} of ${n}</span></div>`;
  }).join('')}</div>`;
  document.getElementById('ff').innerHTML = side('off', 'Canada on offence') + side('def', 'Canada on defence');
})();
sortable(document.getElementById('factors'), [
  { key: 'team_name', label: 'Team', l: true, fmt: (v, r) => `${flag(r.iso)}${v}` },
  ...FACTORS.map(([k, l, hib, s]) => ({
    key: k, label: (s === 'def' ? 'Def ' : 'Off ') + l.match(/\((.*)\)/)[1],
    fmt: v => fmt(v), lowerBetter: !hib, tip: l + (hib ? '' : ' (lower is better)'),
  })),
], DATA.teams, { key: 'efg', desc: true }, document.getElementById('factors-more'));

teamScatter(document.getElementById('scatter-ff'), {
  x: 'extra_poss', y: 'efg', xPad: 1.5, yPad: 2, xStep: 5, yStep: 5,
  xFmt: v => v > 0 ? '+' + v : v, yFmt: v => v + '%',
  xLabel: 'Extra possessions per game (OREB + TOV forced − opp OREB − TOV) →',
  yLabel: 'Effective FG%',
  good: 'MORE SHOTS, BETTER SHOOTING', bad: 'FEWER SHOTS, WORSE SHOOTING',
  aria: 'Extra possessions per game against effective field goal percentage for every team',
  tip: t => `Extra possessions ${t.extra_poss > 0 ? '+' : ''}${fmt(t.extra_poss)}/game · eFG ${fmt(t.efg)}%`,
});

// Player leaders
const STATS = [
  ['ppg', 'Points'], ['rpg', 'Rebounds'], ['apg', 'Assists'], ['eff', 'Efficiency'],
  ['spg', 'Steals'], ['bpg', 'Blocks'], ['ts', 'TS%'], ['fg3', '3P%'],
];
const P = DATA.players;
const ptable = sortable(document.getElementById('players'), [
  { key: 'player_name', label: 'Player', l: true },
  { key: 'team_code', label: 'Team', l: true, fmt: (v, r) => `${flag(r.iso)}${v}` },
  { key: 'venue', label: 'Venue', l: true },
  { key: 'gp', label: 'GP' },
  ...STATS.map(([k, l]) => ({ key: k, label: l === 'Efficiency' ? 'EFF' : l === 'Points' ? 'PTS' : l === 'Rebounds' ? 'REB' : l === 'Assists' ? 'AST' : l === 'Steals' ? 'STL' : l === 'Blocks' ? 'BLK' : l, fmt: v => fmt(v) })),
], P, { key: 'ppg', desc: true }, document.getElementById('players-more'));
const tabs = document.getElementById('ptabs');
tabs.innerHTML = STATS.map(([k, l], i) => `<button class="tab ${i ? '' : 'on'}" data-k="${k}">${l}</button>`).join('');
tabs.querySelectorAll('.tab').forEach(b => b.onclick = () => {
  tabs.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === b));
  ptable.set(b.dataset.k, true);
});
</script>
</body>
</html>
"""
