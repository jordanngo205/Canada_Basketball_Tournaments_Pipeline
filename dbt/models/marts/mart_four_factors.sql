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

),

-- The factors themselves, unrounded. The z-scores below are built on these,
-- so rounding happens once, at the end.
rates as (

    select
        *,
        100.0 * (fgm + 0.5 * fg3m) / nullif(fga, 0)             as efg,
        100.0 * oreb / nullif(oreb + opp_dreb, 0)               as orb,
        100.0 * fta / nullif(fga, 0)                            as ftr,
        -- Oliver's TOV%: turnovers per play, where a play is a shot, a trip
        -- to the line or a turnover. Not per estimated possession — that
        -- denominator nets out possessions extended by offensive rebounds,
        -- which shrinks it and inflates the rate by three or four points.
        -- Canada at the Olympic Pre-Qualifier is 16.9% this way, matching the
        -- coaching staff's sheet; per possession it read 20.7%.
        100.0 * tov / nullif(fga + 0.44 * fta + tov, 0)         as tovr,

        100.0 * (opp_fgm + 0.5 * opp_fg3m) / nullif(opp_fga, 0) as opp_efg,
        100.0 * opp_oreb / nullif(opp_oreb + dreb, 0)           as opp_orb,
        100.0 * opp_fta / nullif(opp_fga, 0)                    as opp_ftr,
        100.0 * opp_tov / nullif(opp_fga + 0.44 * opp_fta + opp_tov, 0) as opp_tovr
    from totals

),

-- Each factor as a z-score within its own tournament, signed so that positive
-- is always good: a low turnover rate scores above zero, and on defence every
-- factor is the opponent's, so conceding less is what scores. Sample standard
-- deviation, as a spreadsheet's STDEV does, so the numbers line up with the
-- coaching staff's sheet.
--
-- The four are summed unweighted. Oliver's 40/25/20/15 weights are a claim
-- about the NBA; with eight teams and a handful of games each, an equal vote
-- is the more honest default.
z as (

    select
        *,
        (efg  - avg(efg)  over c) / nullif(stddev_samp(efg)  over c, 0) as z_efg,
        (orb  - avg(orb)  over c) / nullif(stddev_samp(orb)  over c, 0) as z_orb,
        (ftr  - avg(ftr)  over c) / nullif(stddev_samp(ftr)  over c, 0) as z_ftr,
        (avg(tovr) over c - tovr) / nullif(stddev_samp(tovr) over c, 0) as z_tov,

        (avg(opp_efg) over c - opp_efg) / nullif(stddev_samp(opp_efg) over c, 0) as z_opp_efg,
        (avg(opp_orb) over c - opp_orb) / nullif(stddev_samp(opp_orb) over c, 0) as z_opp_orb,
        (avg(opp_ftr) over c - opp_ftr) / nullif(stddev_samp(opp_ftr) over c, 0) as z_opp_ftr,
        (opp_tovr - avg(opp_tovr) over c) / nullif(stddev_samp(opp_tovr) over c, 0) as z_opp_tov
    from rates
    window c as (partition by competition)

),

scored as (

    select
        *,
        z_efg + z_orb + z_ftr + z_tov                     as z_off,
        z_opp_efg + z_opp_orb + z_opp_ftr + z_opp_tov     as z_def
    from z

)

select
    s.competition,
    s.team_id,
    s.team_code,
    s.team_name,
    s.games,
    s.wins,
    s.losses,

    round(100.0 * pts / nullif(poss, 0), 1)                         as ortg,
    round(100.0 * opp_pts / nullif(opp_poss, 0), 1)                 as drtg,
    round(100.0 * pts / nullif(poss, 0)
        - 100.0 * opp_pts / nullif(opp_poss, 0), 1)                 as net_rtg,
    round(poss::numeric / nullif(games, 0), 1)                      as pace,

    -- 1. Shooting. Weighted about 40% of what decides games.
    round(efg, 1)                                                   as efg_pct,
    round(opp_efg, 1)                                               as opp_efg_pct,

    -- 2. Turnovers, as a share of plays rather than a per-game count.
    round(tovr, 1)                                                  as tov_pct,
    round(opp_tovr, 1)                                              as opp_tov_pct,

    -- 3. Rebounding. Offensive boards as a share of the ones actually
    -- available, which is your misses — a team that misses more has more
    -- chances and a raw count would flatter them.
    round(orb, 1)                                                   as oreb_pct,
    round(100.0 * dreb / nullif(dreb + opp_oreb, 0), 1)             as dreb_pct,
    round(opp_orb, 1)                                               as opp_oreb_pct,

    -- 4. Free throws. FTA per FGA measures how often you get to the line at
    -- all; FTM per FGA folds in whether you convert. Both are reported since
    -- they answer different questions.
    round(ftr, 1)                                                   as ft_rate,
    round(100.0 * ftm / nullif(fga, 0), 1)                          as ft_made_rate,
    round(opp_ftr, 1)                                               as opp_ft_rate,

    -- Net: each factor at your end less the same factor at the other, so
    -- positive is good throughout. For turnovers that means forced minus
    -- committed. Taken from the unrounded rates, so it can differ by 0.1 from
    -- subtracting the two rounded columns.
    round(efg - opp_efg, 1)                                         as net_efg_pct,
    round(orb - opp_orb, 1)                                         as net_oreb_pct,
    round(ftr - opp_ftr, 1)                                         as net_ft_rate,
    round(opp_tovr - tovr, 1)                                       as net_tov_pct,

    -- Extra possessions: offensive boards plus turnovers forced, less the
    -- same conceded, per game. Per game rather than a total because teams
    -- here play three to seven games, and a total would rank a deep run.
    round((oreb + opp_tov - opp_oreb - tov)::numeric / nullif(games, 0), 1)
                                                                    as extra_poss_pg,

    round(z_efg, 2)      as z_efg,
    round(z_orb, 2)      as z_oreb,
    round(z_ftr, 2)      as z_ft_rate,
    round(z_tov, 2)      as z_tov,
    round(z_opp_efg, 2)  as z_opp_efg,
    round(z_opp_orb, 2)  as z_opp_oreb,
    round(z_opp_ftr, 2)  as z_opp_ft_rate,
    round(z_opp_tov, 2)  as z_opp_tov,
    round(z_off, 2)          as z_off,
    round(z_def, 2)          as z_def,
    round(z_off + z_def, 2)  as z_total,

    -- The projection: the whole field ordered by combined z-score, against
    -- where each team actually finished.
    rank() over (partition by s.competition order by z_off + z_def desc nulls last)
                                                                    as proj_rank,
    p.placement,

    -- Where a team sits on each factor within its own tournament. Rank is more
    -- readable than the raw rate when comparing across competitions of very
    -- different standard — a 48% eFG means something different at the U17s.
    rank() over (partition by s.competition order by efg desc)     as efg_rank,
    rank() over (partition by s.competition order by tovr asc)     as tov_rank,
    rank() over (partition by s.competition order by orb desc)     as oreb_rank,
    rank() over (partition by s.competition order by ftr desc)     as ft_rank

from scored s
left join {{ ref('mart_final_placement') }} p
  on  p.competition = s.competition
 and  p.team_id     = s.team_id
