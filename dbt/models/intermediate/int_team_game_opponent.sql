-- Each team-game next to what the opponent did in the same game.
--
-- You can't work out a defensive rating from your own row, and nearly every
-- rating needs both sides. Doing the self-join once here keeps the marts
-- readable and stops three of them writing slightly different join conditions.
--
-- Possessions use Dean Oliver's estimate with the offensive-rebound
-- adjustment, averaged across both teams:
--
--   0.5 * ( FGA + 0.4*FTA - 1.07*(OR/(OR+opp_DR))*(FGA-FGM) + TO
--         + the same expression from the opponent's side )
--
-- Two things matter here. The rebound term only discounts the misses a team
-- actually rebounded itself, which a flat "minus OR" overstates. And averaging
-- the two sides gives both teams one shared possession count — they trade
-- possessions, so a game where the two disagree is an artefact of the
-- estimator rather than a fact about the game.
--
-- This is deliberately the same formula the published Canada Basketball
-- dashboards use, so the two report identical ratings. An earlier version here
-- used the simpler FGA - OR + TO + 0.44*FTA and came out 2-3 possessions
-- adrift, which moved every rating by about two points.

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
    -- Not rounded. Rounding to whole possessions before dividing moves every
    -- rating by two or three tenths, which is enough to disagree with the
    -- published dashboards on numbers that should match exactly.
    round(0.5 * (
        (t.fga + 0.4 * t.fta
           - 1.07 * (t.oreb::numeric / nullif(t.oreb + o.dreb, 0)) * (t.fga - t.fgm)
           + t.tov)
      + (o.fga + 0.4 * o.fta
           - 1.07 * (o.oreb::numeric / nullif(o.oreb + t.dreb, 0)) * (o.fga - o.fgm)
           + o.tov)
    ), 2)                                            as possessions,
    -- Same number by construction: both teams share the averaged estimate.
    round(0.5 * (
        (t.fga + 0.4 * t.fta
           - 1.07 * (t.oreb::numeric / nullif(t.oreb + o.dreb, 0)) * (t.fga - t.fgm)
           + t.tov)
      + (o.fga + 0.4 * o.fta
           - 1.07 * (o.oreb::numeric / nullif(o.oreb + t.dreb, 0)) * (o.fga - o.fgm)
           + o.tov)
    ), 2)                                            as opp_possessions

from team_games t
join team_games o
  on  o.game_id = t.game_id
 and  o.team_id <> t.team_id
