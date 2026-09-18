-- Can't make more than you attempt, and FG has to be 2PT + 3PT.

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
