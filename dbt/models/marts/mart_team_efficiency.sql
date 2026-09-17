-- One row per team per game, with the rate stats a scouting page actually shows.
--
-- Everything here is per-100-possessions rather than per-game, because pace
-- varies enough between FIBA sides that raw totals mislead: a team that plays
-- fast looks better on points and worse on defence than it is.

select
    game_id,
    team_id,
    team_code,
    team_name,
    game_date,
    competition,
    round_name,
    group_code,
    opp_team_code,
    win,
    loss,
    margin,

    pts,
    opp_pts,
    possessions,

    -- Offensive and defensive rating: points scored / allowed per 100 trips.
    round(100.0 * pts / nullif(possessions, 0), 1)          as ortg,
    round(100.0 * opp_pts / nullif(opp_possessions, 0), 1)  as drtg,
    round(100.0 * pts / nullif(possessions, 0)
        - 100.0 * opp_pts / nullif(opp_possessions, 0), 1)  as net_rtg,

    -- Effective field goal % credits a three as 1.5 twos, which is the whole
    -- point of shooting them.
    round(100.0 * (fgm + 0.5 * fg3m) / nullif(fga, 0), 1)   as efg_pct,
    round(100.0 * fg3m / nullif(fg3a, 0), 1)                as fg3_pct,
    round(100.0 * ftm / nullif(fta, 0), 1)                  as ft_pct,

    round(100.0 * tov / nullif(possessions, 0), 1)          as tov_pct,

    -- Offensive rebound rate is share of available misses, not a raw count:
    -- a team that misses a lot has more chances to rebound its own shot.
    round(100.0 * oreb / nullif(oreb + opp_dreb, 0), 1)     as oreb_pct,
    round(100.0 * dreb / nullif(dreb + opp_oreb, 0), 1)     as dreb_pct,

    -- How often a made basket was set up, a rough read on ball movement.
    round(100.0 * ast / nullif(fgm, 0), 1)                  as ast_to_fgm_pct,

    round(100.0 * paint_fgm / nullif(fgm, 0), 1)            as pct_fgm_in_paint,
    round(2.0 * fg2m + 3.0 * fg3m, 0)                       as pts_from_field,
    ftm                                                     as pts_from_ft

from {{ ref('int_team_game_opponent') }}
