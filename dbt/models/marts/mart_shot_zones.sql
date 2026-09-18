-- Shooting by zone, per team per tournament — the numbers behind a shot chart.
--
-- Points per attempt is the column to read, not FG%. A 33% three is worth
-- 1.00 points per shot and a 45% mid-range is worth 0.90, so the lower
-- percentage is the better shot. FG% alone hides that entirely.

select
    competition,
    team_code,
    shot_zone,
    count(*)                                                       as attempts,
    count(*) filter (where shot_made)                              as makes,
    round(100.0 * count(*) filter (where shot_made) / count(*), 1) as fg_pct,
    round(avg(shot_distance), 1)                                   as avg_distance,

    round(sum(case when shot_made then shot_value else 0 end)::numeric
          / count(*), 2)                                           as pts_per_attempt,

    -- What share of the team's shots come from here. Shot selection, separate
    -- from how well they shoot it.
    round(100.0 * count(*) / sum(count(*)) over (
        partition by competition, team_code), 1)                   as share_of_shots

from {{ ref('int_shots') }}
where team_code is not null
group by competition, team_code, shot_zone
