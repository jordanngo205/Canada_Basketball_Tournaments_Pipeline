-- A team's final score has to equal the sum of its per-period points.
--
-- Probably the most useful test here: the two numbers come from different
-- parts of the payload (gameDetails.c[side].Score vs quartersScores), so if
-- either parse drifts this catches it.
--
-- Singular tests pass on zero rows.

with period_totals as (

    select
        game_id,
        sum(home_points) as home_period_total,
        sum(away_points) as away_period_total
    from {{ ref('stg_period_scores') }}
    group by game_id

)

select
    g.game_id,
    g.home_code,
    g.home_score,
    p.home_period_total,
    g.away_code,
    g.away_score,
    p.away_period_total
from {{ ref('stg_games') }} g
join period_totals p using (game_id)
where g.home_score != p.home_period_total
   or g.away_score != p.away_period_total
