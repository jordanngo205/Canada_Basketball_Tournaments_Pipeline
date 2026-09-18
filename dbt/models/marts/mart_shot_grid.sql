-- Shots binned onto a court grid — the data behind a shot chart.
--
-- 10x10 bins of 28 units each over the 0-280 coordinate space.
--
-- Started at 14x14 and it was too fine: a team takes ~570 shots in a
-- tournament, so most bins held four or five and the colours swung wildly
-- between neighbours. That reads as a pattern when it is sampling noise. At
-- 28 units a bin is about 1.5m square and carries enough shots to mean
-- something. Empty bins aren't emitted — a shot chart shouldn't carry rows
-- for places nobody shot from.
--
-- Efficiency is reported as points per attempt against the same bin's
-- tournament-wide average, because raw PPA has no meaning on its own — 0.9 is
-- poor at the rim and excellent from the corner.

with binned as (

    select
        competition,
        team_code,
        width_bucket(shot_x, 0, 280, 10) as bin_x,
        width_bucket(shot_y, 0, 280, 10) as bin_y,
        shot_made,
        shot_value
    from {{ ref('int_shots') }}
    where team_code is not null
      and shot_x between 0 and 280
      and shot_y between 0 and 280

),

per_bin as (

    select
        competition,
        team_code,
        bin_x,
        bin_y,
        count(*)                                                        as attempts,
        count(*) filter (where shot_made)                               as makes,
        sum(case when shot_made then shot_value else 0 end)             as points
    from binned
    group by competition, team_code, bin_x, bin_y

),

-- The tournament's own baseline for each bin. Comparing a team to the field it
-- actually played is the only comparison that means anything here.
league_bin as (

    select
        competition,
        bin_x,
        bin_y,
        sum(points)::numeric / nullif(sum(attempts), 0) as league_ppa,
        sum(attempts)                                   as league_attempts
    from per_bin
    group by competition, bin_x, bin_y

)

select
    b.competition,
    b.team_code,
    b.bin_x,
    b.bin_y,

    -- Centre of the bin in court units, so the front end can place a mark
    -- without knowing the binning scheme.
    (b.bin_x - 0.5) * 28 as x,
    (b.bin_y - 0.5) * 28 as y,

    b.attempts,
    b.makes,
    b.points,
    round(100.0 * b.makes / b.attempts, 1)                    as fg_pct,
    round(b.points::numeric / b.attempts, 2)                  as pts_per_attempt,
    round(l.league_ppa, 2)                                    as league_ppa,
    round(b.points::numeric / b.attempts - l.league_ppa, 2)   as ppa_vs_league,
    l.league_attempts

from per_bin b
join league_bin l using (competition, bin_x, bin_y)
