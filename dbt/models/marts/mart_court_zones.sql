-- The twelve-zone court breakdown, per team per tournament.
--
-- This is what a shot chart actually shows: named regions of the floor with a
-- shooting percentage on each, shaded against what everyone else manages from
-- the same region. Scattered dots look busier but tell you less — a zone with
-- 60 attempts behind it says something a cloud of five-shot patches can't.

with shots as (

    select * from {{ ref('int_shots') }}
    where team_code is not null and court_zone is not null

),

per_team as (

    select
        competition,
        team_code,
        court_zone,
        count(*)                                            as attempts,
        count(*) filter (where shot_made)                   as makes,
        sum(case when shot_made then shot_value else 0 end) as points
    from shots
    group by competition, team_code, court_zone

),

-- Everyone else in the same tournament, from the same zone. The comparison is
-- the whole point: 34% is poor from the rim and excellent from the corner.
league as (

    select
        competition,
        court_zone,
        sum(attempts)                                   as lg_attempts,
        sum(makes)                                      as lg_makes,
        sum(points)::numeric / nullif(sum(attempts), 0) as lg_ppa
    from per_team
    group by competition, court_zone

)

select
    t.competition,
    t.team_code,
    t.court_zone,
    t.attempts,
    t.makes,
    round(100.0 * t.makes / t.attempts, 1)                        as fg_pct,
    round(t.points::numeric / t.attempts, 2)                      as pts_per_attempt,
    round(100.0 * l.lg_makes / nullif(l.lg_attempts, 0), 1)       as league_fg_pct,
    round(l.lg_ppa, 2)                                            as league_ppa,
    round(100.0 * t.makes / t.attempts
          - 100.0 * l.lg_makes / nullif(l.lg_attempts, 0), 1)     as fg_pct_vs_league,
    round(t.points::numeric / t.attempts - l.lg_ppa, 2)           as ppa_vs_league,
    round(100.0 * t.attempts / sum(t.attempts) over (
        partition by t.competition, t.team_code), 1)              as share_of_shots,
    l.lg_attempts                                                 as league_attempts
from per_team t
join league l using (competition, court_zone)
