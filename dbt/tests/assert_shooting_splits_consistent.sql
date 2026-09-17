-- Makes cannot exceed attempts, and total field goals must be the sum of the
-- two- and three-point components.

select
    game_id,
    person_id,
    player_name,
    fgm, fga, fg2m, fg2a, fg3m, fg3a, ftm, fta
from {{ ref('stg_player_box') }}
where fgm > fga
   or ftm > fta
   or fg2m > fg2a
   or fg3m > fg3a
   or fgm != (fg2m + fg3m)
   or fga != (fg2a + fg3a)
   or reb != (oreb + dreb)
