-- One row per player per competition: the leaderboard behind the dashboard.
--
-- Only games the player actually appeared in count toward an average. FIBA
-- lists the full twelve-player roster for every game, so including did-not-play
-- rows would quietly drag every average down for squad players.
--
-- Per-minute rates are deliberately absent. FIBA leaves minutes null in the
-- box-score payload, so they have to be derived from play-by-play substitution
-- events before any per-minute stat can be trusted. Publishing a per-minute
-- number computed from a null would be worse than not having one.

with appearances as (

    select
        b.person_id,
        b.player_name,
        b.position,
        b.jersey,
        b.team_id,
        g.competition,
        case when b.side = 'home' then g.home_team else g.away_team end as team_name,
        case when b.side = 'home' then g.home_code else g.away_code end as team_code,
        b.is_starter,
        b.pts, b.reb, b.oreb, b.dreb, b.ast, b.stl, b.blk, b.tov, b.pf,
        b.fgm, b.fga, b.fg2m, b.fg2a, b.fg3m, b.fg3a, b.ftm, b.fta,
        b.efficiency, b.plus_minus
    from {{ ref('stg_player_box') }} b
    join {{ ref('stg_games') }} g using (game_id)
    where b.has_played

)

select
    competition,
    person_id,

    -- Grouped on person_id alone, deliberately. FIBA spells the same player
    -- differently between games in the same tournament — 202510 is "Pako
    -- Saldivar" in two games and "Pako Cruz" in six, 418801 is "Samuel
    -- Hincapie" once and "Samuel Hincapie Alzate" twice. Including the name in
    -- the grouping splits them into two half-players. The id is the identity;
    -- everything else is an attribute, so take the most common value.
    mode() within group (order by player_name) as player_name,
    mode() within group (order by team_code)   as team_code,
    mode() within group (order by team_name)   as team_name,
    mode() within group (order by position)    as position,
    min(jersey)                                as jersey,

    count(*)                               as games_played,
    sum(is_starter::int)                   as games_started,

    sum(pts)                               as total_pts,
    round(avg(pts), 1)                     as ppg,
    round(avg(reb), 1)                     as rpg,
    round(avg(ast), 1)                     as apg,
    round(avg(stl), 1)                     as spg,
    round(avg(blk), 1)                     as bpg,
    round(avg(tov), 1)                     as topg,
    round(avg(efficiency), 1)              as eff_per_game,
    round(avg(plus_minus), 1)              as plus_minus_per_game,

    -- Shooting is aggregated from totals, never averaged from per-game
    -- percentages: a 1-for-1 night would otherwise count as much as a
    -- 10-for-20 one.
    sum(fgm) as fgm, sum(fga) as fga,
    sum(fg3m) as fg3m, sum(fg3a) as fg3a,
    sum(ftm) as ftm, sum(fta) as fta,
    round(100.0 * sum(fgm) / nullif(sum(fga), 0), 1)                  as fg_pct,
    round(100.0 * sum(fg3m) / nullif(sum(fg3a), 0), 1)                as fg3_pct,
    round(100.0 * sum(ftm) / nullif(sum(fta), 0), 1)                  as ft_pct,
    round(100.0 * (sum(fgm) + 0.5 * sum(fg3m)) / nullif(sum(fga), 0), 1) as efg_pct,

    -- True shooting counts free throws, so it is the fairer single number for
    -- comparing a rim-running centre with a shooter.
    round(
        100.0 * sum(pts) / nullif(2.0 * (sum(fga) + 0.44 * sum(fta)), 0), 1
    ) as ts_pct,

    rank() over (partition by competition order by avg(pts) desc) as ppg_rank

from appearances
group by competition, person_id
