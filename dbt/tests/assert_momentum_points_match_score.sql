-- The momentum chart's running score is rebuilt from the made shots in
-- mart_game_momentum. If those don't add up to the final score, the chart's
-- hover would show a scoreline that never happened.

with chart as (

    select game_id, side, sum(points) as points
    from {{ ref('mart_game_momentum') }}
    group by game_id, side

),

final as (

    select game_id, 'home' as side, home_score as points from {{ ref('stg_games') }}
    union all
    select game_id, 'away', away_score from {{ ref('stg_games') }}

)

select f.game_id, f.side, f.points as final_points, c.points as chart_points
from final f
join chart c using (game_id, side)
where c.points != f.points
