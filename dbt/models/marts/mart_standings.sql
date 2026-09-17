-- Tournament standings, one row per team per competition.
--
-- Ordering matches how FIBA presents a group table: wins first, then point
-- differential. Real FIBA tiebreaks run on head-to-head mini-leagues between
-- the tied teams, which needs the full fixture list rather than a running
-- total, so this is the display order and not an official tiebreak.

with team_games as (

    select * from {{ ref('int_team_game_opponent') }}

),

aggregated as (

    select
        competition,
        group_code,
        team_id,
        team_code,
        team_name,
        count(*)                       as games_played,
        sum(win)                       as wins,
        sum(loss)                      as losses,
        sum(pts)                       as points_for,
        sum(opp_pts)                   as points_against,
        sum(margin)                    as point_differential,
        round(avg(pts), 1)             as ppg,
        round(avg(opp_pts), 1)         as opp_ppg,
        round(100.0 * sum(pts) / nullif(sum(possessions), 0), 1)         as ortg,
        round(100.0 * sum(opp_pts) / nullif(sum(opp_possessions), 0), 1) as drtg
    from team_games
    group by competition, group_code, team_id, team_code, team_name

)

select
    *,
    ortg - drtg as net_rtg,
    round(100.0 * wins / nullif(games_played, 0), 1) as win_pct,
    row_number() over (
        partition by competition, group_code
        order by wins desc, point_differential desc, points_for desc
    ) as standing_rank
from aggregated
