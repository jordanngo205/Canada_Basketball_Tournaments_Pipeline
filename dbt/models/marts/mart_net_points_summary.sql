-- Net points rolled up to one row per player per tournament.
--
-- The per-game table is 3,674 rows; a dashboard wants the season shape. Both
-- halves are summed rather than averaged, because net points is already a
-- cumulative quantity — a player who was +30 across eight games contributed
-- more than one who was +8 in two, and averaging hides that.

select
    competition,
    person_id,
    player_name,
    team_code,
    count(*)                        as games,
    sum(case when is_starter then 1 else 0 end) as starts,
    sum(net_points)                 as net_points,
    round(sum(off_net), 1)          as off_net,
    round(sum(def_net), 1)          as def_net,
    round(avg(net_points), 1)       as net_per_game,
    sum(pts)                        as pts,
    sum(reb)                        as reb,
    sum(ast)                        as ast
from {{ ref('mart_player_net_points') }}
group by competition, person_id, player_name, team_code
having count(*) >= 2
