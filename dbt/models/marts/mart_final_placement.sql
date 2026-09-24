-- Where each team finished, per tournament.
--
-- Placement is what the four-factors projection gets checked against, so it
-- has to be the real finishing order, not the group table. Three sources, in
-- order of precedence:
--
--   1. seeds/final_placements.csv — stated by hand. Needed wherever the games
--      alone don't settle it: the Olympic Pre-Qualifier played no 3rd-place or
--      classification games, so its two beaten semi-finalists and its four
--      group-phase exits have no game between them to decide the order.
--   2. Placement games, forfeits included. 'Final', '3rd Place Game' and any 'Class 5-6' style
--      round is a straight fight for two adjacent places: the winner takes the
--      upper one. Brackets like 'Class 9-16' decide nothing on their own.
--   3. A single round-robin group with no knockouts is its own final table,
--      with FIBA's head-to-head tiebreak applied.
--
-- Anything none of those cover stays null rather than being guessed at.

with games as (

    select * from {{ ref('int_team_game_opponent') }}

),

teams as (

    select distinct competition, team_id, team_code
    from games

),

-- Every result that can decide a placement: the games with a box score, plus
-- forfeits, which FIBA scores but publishes no box score for. Without the
-- second half a forfeited 5th-place game would leave both teams to be placed
-- by the leftover rule below, possibly the wrong way round.
results as (

    select competition, team_id, round_name, win
    from games

    union all

    select f.competition, t.team_id, f.round_name,
           case when f.home_score > f.away_score then 1 else 0 end
    from {{ ref('stg_result_only_games') }} f
    join teams t
      on  t.competition = f.competition
     and  t.team_code   = f.home_code

    union all

    select f.competition, t.team_id, f.round_name,
           case when f.away_score > f.home_score then 1 else 0 end
    from {{ ref('stg_result_only_games') }} f
    join teams t
      on  t.competition = f.competition
     and  t.team_code   = f.away_code

),

placement_games as (

    select
        competition,
        team_id,
        win,
        case
            when round_name = 'Final' then 1
            when round_name = '3rd Place Game' then 3
            else substring(round_name from '^Class(?:ification)? (\d+)-\d+$')::int
        end as upper_place,
        case
            when round_name = 'Final' then 2
            when round_name = '3rd Place Game' then 4
            else substring(round_name from '^Class(?:ification)? \d+-(\d+)$')::int
        end as lower_place
    from results

),

from_games as (

    select
        competition,
        team_id,
        case when win = 1 then upper_place else lower_place end as placement
    from placement_games
    where lower_place = upper_place + 1

),

-- A single round-robin group with no knockouts. FIBA separates teams level on
-- wins by a mini-league of only the games between them, then the margin in
-- those games, and only then overall margin. mart_standings orders by overall
-- margin alone, which is fine for display but wrong for a final placing: at
-- WC Qualifying Türkiye Canada, Japan and Türkiye all went 2-3, and Canada's
-- +44 would put them third when their 0-2 against the other two made them
-- fifth.
round_robin_comps as (

    select competition
    from games
    group by competition
    having count(distinct group_code) = 1
       and count(*) filter (where group_code is null) = 0

),

rr_table as (

    select s.competition, s.team_id, s.wins, s.point_differential
    from {{ ref('mart_standings') }} s
    join round_robin_comps c using (competition)

),

head_to_head as (

    select
        g.competition,
        g.team_id,
        sum(g.win)    as h2h_wins,
        sum(g.margin) as h2h_margin
    from games g
    join rr_table t
      on  t.competition = g.competition
     and  t.team_id     = g.team_id
    join rr_table o
      on  o.competition = g.competition
     and  o.team_id     = g.opp_team_id
    where t.wins = o.wins
    group by g.competition, g.team_id

),

round_robin as (

    select
        t.competition,
        t.team_id,
        row_number() over (
            partition by t.competition
            order by t.wins desc,
                     coalesce(h.h2h_wins, 0) desc,
                     coalesce(h.h2h_margin, 0) desc,
                     t.point_differential desc
        ) as placement
    from rr_table t
    left join head_to_head h
      on  h.competition = t.competition
     and  h.team_id     = t.team_id

),

stated as (

    select competition, team_code, placement
    from {{ ref('final_placements') }}

),

placed as (

    select
        t.competition,
        t.team_id,
        t.team_code,
        coalesce(s.placement, g.placement, r.placement) as placement,
        case
            when s.placement is not null then 'stated'
            when g.placement is not null then 'placement_game'
            when r.placement is not null then 'round_robin'
        end as placement_source
    from teams t
    left join stated s
      on  s.competition = t.competition
     and  s.team_code   = t.team_code
    left join from_games g
      on  g.competition = t.competition
     and  g.team_id     = t.team_id
    left join round_robin r
      on  r.competition = t.competition
     and  r.team_id     = t.team_id

),

-- Teams no game placed: the ones knocked out in the group phase with no
-- classification round (9th and 10th at the 2025 Women's AmeriCup), or left
-- without an opponent (Argentina at the 2025 U19 World Cup, where only 15
-- teams played and the 15th-16th game never happened). They take the places
-- nobody else holds, in order of group finish, then wins, then margin.
--
-- Only once the Final is in. Before that, an unplaced team is usually just a
-- team whose placement game hasn't been played yet.
finished as (

    select distinct competition
    from results
    where round_name = 'Final'

),

open_places as (

    select c.competition, p.place,
           row_number() over (partition by c.competition order by p.place) as k
    from (select competition, count(*) as n from placed group by competition) c
    join finished using (competition)
    cross join lateral generate_series(1, c.n) as p(place)
    where not exists (
        select 1 from placed x
        where x.competition = c.competition and x.placement = p.place
    )

),

leftover as (

    select
        p.competition,
        p.team_id,
        row_number() over (
            partition by p.competition
            order by s.standing_rank, s.wins desc, s.point_differential desc
        ) as k
    from placed p
    join finished using (competition)
    left join {{ ref('mart_standings') }} s
      on  s.competition = p.competition
     and  s.team_id     = p.team_id
    where p.placement is null

)

select
    p.competition,
    p.team_id,
    p.team_code,
    coalesce(p.placement, o.place) as placement,
    coalesce(p.placement_source, case when o.place is not null then 'remaining' end)
        as placement_source
from placed p
left join leftover l
  on  l.competition = p.competition
 and  l.team_id     = p.team_id
left join open_places o
  on  o.competition = l.competition
 and  o.k           = l.k
