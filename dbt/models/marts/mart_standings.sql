-- Group tables, one row per team per competition.
--
-- Standings cover the GROUP PHASE only. Knockout fixtures carry no group code,
-- and folding them in would both corrupt the table a group standing is meant
-- to show and split each qualifying team across two rows — a group row and a
-- knockout row. The team's overall tournament record is still available in the
-- `overall_*` columns for anywhere that wants it.
--
-- Ordering follows how FIBA presents a group: wins, then point differential.
-- Real FIBA tiebreaks run a head-to-head mini-league between only the tied
-- teams, which needs the full fixture list rather than a running total, so this
-- is display order and not an official tiebreak.

with team_games as (

    select * from {{ ref('int_team_game_opponent') }}

),

group_phase as (

    select
        competition,
        group_code,
        team_id,
        team_code,
        team_name,
        count(*)               as games_played,
        sum(win)               as wins,
        sum(loss)              as losses,
        sum(pts)               as points_for,
        sum(opp_pts)           as points_against,
        sum(margin)            as point_differential,
        round(avg(pts), 1)     as ppg,
        round(avg(opp_pts), 1) as opp_ppg,
        round(100.0 * sum(pts) / nullif(sum(possessions), 0), 1)         as ortg,
        round(100.0 * sum(opp_pts) / nullif(sum(opp_possessions), 0), 1) as drtg
    from team_games
    where group_code is not null
    group by competition, group_code, team_id, team_code, team_name

),

-- Every game the team played in the competition, knockouts included.
overall as (

    select
        competition,
        team_id,
        count(*)    as overall_games,
        sum(win)    as overall_wins,
        sum(loss)   as overall_losses,
        sum(margin) as overall_point_differential
    from team_games
    group by competition, team_id

)

select
    g.competition,
    g.group_code,
    g.team_id,
    g.team_code,
    g.team_name,

    g.games_played,
    g.wins,
    g.losses,
    g.points_for,
    g.points_against,
    g.point_differential,
    g.ppg,
    g.opp_ppg,
    g.ortg,
    g.drtg,
    g.ortg - g.drtg                                    as net_rtg,
    round(100.0 * g.wins / nullif(g.games_played, 0), 1) as win_pct,

    o.overall_games,
    o.overall_wins,
    o.overall_losses,
    o.overall_point_differential,

    row_number() over (
        partition by g.competition, g.group_code
        order by g.wins desc, g.point_differential desc, g.points_for desc
    ) as standing_rank

from group_phase g
join overall o
  on  o.competition = g.competition
 and  o.team_id     = g.team_id
