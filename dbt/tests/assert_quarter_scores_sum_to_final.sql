-- The test you asked for: a team's final score must equal the sum of the
-- points it scored in each period.
--
-- This is the highest-value test in the project because it reconciles two
-- independently-parsed parts of the payload. `final_score` comes from
-- gameDetails.c[side].Score; the period points come from
-- gameDetails.quartersScores. If either parse drifts, this fails.
--
-- A dbt singular test passes when it returns zero rows.

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
