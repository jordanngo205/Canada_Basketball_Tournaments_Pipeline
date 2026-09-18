-- Each team-game next to what the opponent did in the same game.
--
-- You can't work out a defensive rating from your own row, and nearly every
-- rating needs both sides. Doing the self-join once here keeps the marts
-- readable and stops three of them writing slightly different join conditions.
--
-- Possessions: FGA - OR + TO + 0.44 * FTA. The 0.44 is the usual fudge for how
-- many free throws actually end a possession — most trips are two shots where
-- only the second ends it, and-ones end nothing, technicals aren't in the flow.

with team_games as (

    select
        b.game_id,
        b.team_id,
        b.side,
        g.game_date,
        g.competition,
        g.round_name,
        g.group_code,
        case when b.side = 'home' then g.home_team else g.away_team end as team_name,
        case when b.side = 'home' then g.home_code else g.away_code end as team_code,
        b.pts, b.reb, b.oreb, b.dreb, b.ast, b.stl, b.blk, b.tov, b.pf,
        b.fgm, b.fga, b.fg2m, b.fg2a, b.fg3m, b.fg3a, b.ftm, b.fta,
        b.paint_fgm, b.paint_fga
    from {{ ref('stg_team_box') }} b
    join {{ ref('stg_games') }} g using (game_id)

)

select
    t.game_id,
    t.team_id,
    t.team_name,
    t.team_code,
    t.side,
    t.game_date,
    t.competition,
    t.round_name,
    t.group_code,

    t.pts, t.reb, t.oreb, t.dreb, t.ast, t.stl, t.blk, t.tov, t.pf,
    t.fgm, t.fga, t.fg2m, t.fg2a, t.fg3m, t.fg3a, t.ftm, t.fta,
    t.paint_fgm, t.paint_fga,

    o.team_id   as opp_team_id,
    o.team_name as opp_team_name,
    o.team_code as opp_team_code,
    o.pts       as opp_pts,
    o.reb       as opp_reb,
    o.oreb      as opp_oreb,
    o.dreb      as opp_dreb,
    o.fgm       as opp_fgm,
    o.fga       as opp_fga,
    o.fg3m      as opp_fg3m,
    o.ftm       as opp_ftm,
    o.fta       as opp_fta,
    o.tov       as opp_tov,

    t.pts - o.pts       as margin,
    (t.pts > o.pts)::int as win,
    (t.pts < o.pts)::int as loss,

    -- Whole numbers. It's an estimate — decimals would imply precision that
    -- isn't there.
    round(t.fga - t.oreb + t.tov + 0.44 * t.fta)     as possessions,
    round(o.fga - o.oreb + o.tov + 0.44 * o.fta)     as opp_possessions

from team_games t
join team_games o
  on  o.game_id = t.game_id
 and  o.team_id <> t.team_id
