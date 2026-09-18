-- One row per field goal attempt, with the zone it came from.
--
-- Zone boundaries were derived from the data, not copied from an NBA chart.
-- Binning 2pt attempts by distance shows the breaks clearly:
--
--     y  2-19   47.8% FG      (tip-ins and putbacks, only 186 of them)
--     y 20-39   53.7% FG      rim
--     y 40-59   39.9% FG      paint, outside the restricted area
--     y 60-79   29.5% FG      the mid-range cliff
--     y 80+     ~30%  FG      mid-range
--
-- The drop from 53.7 to 39.9 to 29.5 is where the zones actually are. Threes
-- split on x rather than y: wide attempts average y=93 (corners), central ones
-- y=164 (above the break), both converting at 30%.

with shots as (

    select
        p.game_id,
        p.person_id,
        p.period_number,
        p.seconds_elapsed,
        p.shot_x,
        p.shot_y,
        p.shot_made,
        p.shot_value,
        p.action_text
    from {{ ref('stg_pbp') }} p
    where p.shot_x is not null
      and p.shot_value in (2, 3)

)

select
    s.game_id,
    g.competition,
    s.person_id,
    b.player_name,
    b.team_id,
    b.side,
    case when b.side = 'home' then g.home_code else g.away_code end as team_code,

    s.period_number,
    s.seconds_elapsed,
    s.shot_x,
    s.shot_y,
    s.shot_made,
    s.shot_value,

    -- Straight-line distance from the basket, which sits at the centre of the
    -- baseline: x=140, y=0. Same arbitrary units as the coordinates.
    round(sqrt(power(s.shot_x - 140, 2) + power(s.shot_y, 2)), 1) as shot_distance,

    case
        when s.shot_value = 3 and (s.shot_x < 60 or s.shot_x > 220) then 'Corner 3'
        when s.shot_value = 3                                       then 'Above the break 3'
        when s.shot_y < 40                                          then 'Rim'
        when s.shot_y < 60                                          then 'Paint'
        else 'Mid-range'
    end as shot_zone,

    s.action_text

from shots s
join {{ ref('stg_games') }} g using (game_id)
left join {{ ref('stg_player_box') }} b
       on b.game_id = s.game_id and b.person_id = s.person_id
