-- Points scored in each period.
--
-- There are two sources for this on the page and they mean different things.
-- playByPlay period scores are RUNNING TOTALS. gameDetails.quartersScores is
-- points scored IN the period. Use the latter.
--
-- Canada, game 135067: 22/19/17/14 here vs 22/40/59/72 there. Final was 72.

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
