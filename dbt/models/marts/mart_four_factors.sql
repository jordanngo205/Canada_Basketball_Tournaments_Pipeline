-- Dean Oliver's four factors, per team per tournament.
--
-- The claim behind them is that basketball outcomes reduce to four things, in
-- descending order of how much they matter: shooting, turnovers, rebounding,
-- free throws. Everything else is downstream of those.
--
-- Each factor is paired with the opponent's version, because giving up a 60%
-- eFG is as much a property of your defence as your own shooting is of your
-- offence. A team that scores well and concedes worse isn't good.

with games as (

    select * from {{ ref('int_team_game_opponent') }}

),

totals as (

    select
        competition,
        team_id,
        team_code,
        team_name,
        count(*)        as games,
        sum(win)        as wins,
        sum(loss)       as losses,

        sum(pts)        as pts,
        sum(fgm)        as fgm,
        sum(fga)        as fga,
        sum(fg3m)       as fg3m,
        sum(ftm)        as ftm,
        sum(fta)        as fta,
        sum(tov)        as tov,
        sum(oreb)       as oreb,
        sum(dreb)       as dreb,
        sum(possessions) as poss,

        sum(opp_pts)    as opp_pts,
        sum(opp_fgm)    as opp_fgm,
        sum(opp_fga)    as opp_fga,
        sum(opp_fg3m)   as opp_fg3m,
        sum(opp_ftm)    as opp_ftm,
        sum(opp_fta)    as opp_fta,
        sum(opp_tov)    as opp_tov,
        sum(opp_oreb)   as opp_oreb,
        sum(opp_dreb)   as opp_dreb,
        sum(opp_possessions) as opp_poss
    from games
    group by competition, team_id, team_code, team_name

)

select
    competition,
    team_id,
    team_code,
    team_name,
    games,
    wins,
    losses,

    round(100.0 * pts / nullif(poss, 0), 1)                         as ortg,
    round(100.0 * opp_pts / nullif(opp_poss, 0), 1)                 as drtg,
    round(100.0 * pts / nullif(poss, 0)
        - 100.0 * opp_pts / nullif(opp_poss, 0), 1)                 as net_rtg,
    round(poss::numeric / nullif(games, 0), 1)                      as pace,

    -- 1. Shooting. Weighted about 40% of what decides games.
    round(100.0 * (fgm + 0.5 * fg3m) / nullif(fga, 0), 1)           as efg_pct,
    round(100.0 * (opp_fgm + 0.5 * opp_fg3m) / nullif(opp_fga, 0), 1) as opp_efg_pct,

    -- 2. Turnovers, as a share of possessions rather than a per-game count.
    round(100.0 * tov / nullif(poss, 0), 1)                         as tov_pct,
    round(100.0 * opp_tov / nullif(opp_poss, 0), 1)                 as opp_tov_pct,

    -- 3. Rebounding. Offensive boards as a share of the ones actually
    -- available, which is your misses — a team that misses more has more
    -- chances and a raw count would flatter them.
    round(100.0 * oreb / nullif(oreb + opp_dreb, 0), 1)             as oreb_pct,
    round(100.0 * dreb / nullif(dreb + opp_oreb, 0), 1)             as dreb_pct,

    -- 4. Free throws. FTA per FGA measures how often you get to the line at
    -- all; FTM per FGA folds in whether you convert. Both are reported since
    -- they answer different questions.
    round(100.0 * fta / nullif(fga, 0), 1)                          as ft_rate,
    round(100.0 * ftm / nullif(fga, 0), 1)                          as ft_made_rate,
    round(100.0 * opp_fta / nullif(opp_fga, 0), 1)                  as opp_ft_rate,

    -- Where a team sits on each factor within its own tournament. Rank is more
    -- readable than the raw rate when comparing across competitions of very
    -- different standard — a 48% eFG means something different at the U17s.
    rank() over (partition by competition order by
        (fgm + 0.5 * fg3m) / nullif(fga, 0) desc)                   as efg_rank,
    rank() over (partition by competition order by
        tov::numeric / nullif(poss, 0) asc)                         as tov_rank,
    rank() over (partition by competition order by
        oreb::numeric / nullif(oreb + opp_dreb, 0) desc)            as oreb_rank,
    rank() over (partition by competition order by
        fta::numeric / nullif(fga, 0) desc)                         as ft_rank

from totals
