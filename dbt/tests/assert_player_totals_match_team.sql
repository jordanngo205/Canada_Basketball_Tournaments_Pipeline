-- Cross-grain reconciliation: the players on a team must account for exactly
-- the team's points and assists.
--
-- This is what catches a roster entry that silently failed to parse — the team
-- total stays right while a player row goes missing, so no single-table test
-- would notice.
--
-- Rebounds are deliberately NOT tested for equality. A team rebound — the ball
-- going out of bounds off the defence, a missed final free throw that deadballs
-- — is credited to the team and to no player, so the team total legitimately
-- runs above the sum of its players. Measured across four games in two
-- tournaments that gap ran 2 to 8 rebounds. What must hold is the direction:
-- the team can never have FEWER rebounds than its players combined.

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
