-- Shooting by zone for individual players — the same view as the team chart,
-- one level down.
--
-- Filtered to players with real volume. A 1-for-1 night from the corner is
-- 3.00 points per attempt and would top every leaderboard, which is noise
-- rather than a finding.

with by_zone as (

    select
        competition,
        person_id,
        player_name,
        team_code,
        shot_zone,
        count(*)                                            as attempts,
        count(*) filter (where shot_made)                   as makes,
        sum(case when shot_made then shot_value else 0 end) as points
    from {{ ref('int_shots') }}
    where player_name is not null
    group by competition, person_id, player_name, team_code, shot_zone

),

totals as (

    select competition, person_id, sum(attempts) as total_attempts
    from by_zone
    group by competition, person_id

)

select
    z.competition,
    z.person_id,
    z.player_name,
    z.team_code,
    z.shot_zone,
    z.attempts,
    z.makes,
    round(100.0 * z.makes / z.attempts, 1)           as fg_pct,
    round(z.points::numeric / z.attempts, 2)         as pts_per_attempt,
    round(100.0 * z.attempts / t.total_attempts, 1)  as share_of_shots,
    t.total_attempts
from by_zone z
join totals t using (competition, person_id)
where t.total_attempts >= 15
