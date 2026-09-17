-- Points scored in each period, one row per game per period per team.
--
-- Two sources on the page disagree in meaning and only one is usable directly:
-- `playByPlay.items.<period>.scoreA` is the RUNNING total at the end of that
-- period, while `gameDetails.quartersScores` holds the points scored IN the
-- period. This model uses the latter. Canada's Q1-Q4 in game 135067 read
-- 22/19/17/14 here and 22/40/59/72 there, against a 72-point final.

with source as (

    select
        game_id,
        payload -> 'gameDetails' -> 'quartersScores' as quarters
    from {{ source('raw', 'latest_games') }}

),

flattened as (

    select
        s.game_id,
        p.period ->> 'period'       as period,
        p.idx                       as period_number,
        (p.period ->> 'teamA')::int as home_points,
        (p.period ->> 'teamB')::int as away_points
    from source s,
         lateral jsonb_array_elements(s.quarters) with ordinality as p(period, idx)

)

select
    game_id,
    period,
    period_number,
    home_points,
    away_points,
    home_points - away_points as home_margin,
    period like 'OT%'         as is_overtime
from flattened
