-- A game must contribute exactly two team rows. One row means half the game
-- parsed; three would mean a duplicate.

select
    game_id,
    count(*) as team_rows
from {{ ref('mart_team_efficiency') }}
group by game_id
having count(*) != 2
