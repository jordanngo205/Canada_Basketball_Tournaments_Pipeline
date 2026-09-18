-- The players have to account for exactly the team's points and assists.
--
-- Catches a roster row that quietly failed to parse: the team total stays
-- right while a player goes missing, so nothing on a single table would spot it.
--
-- Rebounds are NOT checked for equality, and this took me a while to work out.
-- Team rebounds — ball out off the defence, missed last free throw — belong to
-- the team and to no player, so the team total legitimately runs higher. Across
-- four games the gap was 2 to 8. Direction still has to hold though: a team
-- can't have fewer rebounds than its players combined.

with player_totals as (

    select
        game_id,
        team_id,
        sum(pts) as pts,
        sum(ast) as ast,
        sum(reb) as reb
    from {{ ref('stg_player_box') }}
    group by game_id, team_id

)

select
    t.game_id,
    t.team_id,
    t.pts as team_pts,
    p.pts as summed_player_pts,
    t.ast as team_ast,
    p.ast as summed_player_ast,
    t.reb as team_reb,
    p.reb as summed_player_reb,
    t.reb - p.reb as team_rebounds
from {{ ref('stg_team_box') }} t
join player_totals p
  on p.game_id = t.game_id
 and p.team_id = t.team_id
where t.pts != p.pts
   or t.ast != p.ast
   or t.reb < p.reb
