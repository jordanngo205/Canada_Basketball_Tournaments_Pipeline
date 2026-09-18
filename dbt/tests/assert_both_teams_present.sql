-- Two team rows per game. One means half of it parsed, three means a dupe.

select
    game_id,
    count(*) as team_rows
from {{ ref('mart_team_efficiency') }}
group by game_id
having count(*) != 2
