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
--   2. Placement games. 'Final', '3rd Place Game' and any 'Class 5-6' style
--      round is a straight fight for two adjacent places: the winner takes the
--      upper one. Brackets like 'Class 9-16' decide nothing on their own.
--   3. A single round-robin group with no knockouts is its own final table.
--
-- Anything none of those cover stays null rather than being guessed at.

with games as (

    select * from {{ ref('int_team_game_opponent') }}

),

teams as (

    select distinct competition, team_id, team_code
    from games

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
    from games

),

from_games as (

    select
        competition,
        team_id,
        case when win = 1 then upper_place else lower_place end as placement
    from placement_games
    where lower_place = upper_place + 1

),

round_robin as (

    select s.competition, s.team_id, s.standing_rank as placement
    from {{ ref('mart_standings') }} s
    where s.competition in (
        select competition
        from games
        group by competition
        having count(distinct group_code) = 1
           and count(*) filter (where group_code is null) = 0
    )

),

stated as (

    select competition, team_code, placement
    from {{ ref('final_placements') }}

)

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
