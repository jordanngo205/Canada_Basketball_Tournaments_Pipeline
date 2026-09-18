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

    -- The twelve-zone breakdown a scout actually reads: restricted area, the
    -- rest of the paint, five mid-range sectors and five from three.
    --
    -- Geometry is FIBA's at 18.67 units per metre (see the header): key 4.9m
    -- wide and 5.8m deep from the baseline, arc at 6.75m, corner cut where the
    -- straight line meets the arc at y = 26.4. Sector boundaries are the angle
    -- from the hoop, 0 at the right baseline running to 180 at the left.
    --
    -- The rim zone is the one place the drawn geometry departs from the rule
    -- book, and deliberately. FIBA's restricted area is 1.25m, but these
    -- coordinates put the median layup at 1.9m — the recorded spot sits
    -- further out than the physical release. At 1.25m the zone caught 17 of
    -- Canada's attempts while "paint" caught 247, which is plainly the rim
    -- hiding in the wrong bucket. The data's own break is at 2.1m: shooting
    -- runs 53.7% inside it and 39.9% just outside. So the rim zone is drawn
    -- where the rim actually is in this coordinate system.
    case
        when sqrt(power(s.shot_x - 140, 2) + power(s.shot_y, 2)) <= 40
            then 'At the Rim'
        when s.shot_x between 94.3 and 185.7 and s.shot_y <= 78.9
            then 'Paint (non-RA)'
        when sqrt(power(s.shot_x - 140, 2) + power(s.shot_y, 2)) < 126 then
            case
                when degrees(atan2(s.shot_y, s.shot_x - 140)) <  36 then 'Mid-Range Right'
                when degrees(atan2(s.shot_y, s.shot_x - 140)) <  72 then 'Mid-Range Right Centre'
                when degrees(atan2(s.shot_y, s.shot_x - 140)) < 108 then 'Mid-Range Centre'
                when degrees(atan2(s.shot_y, s.shot_x - 140)) < 144 then 'Mid-Range Left Centre'
                else 'Mid-Range Left'
            end
        when s.shot_y < 26.4
            then case when s.shot_x > 140 then 'Right Corner 3' else 'Left Corner 3' end
        else
            case
                when degrees(atan2(s.shot_y, s.shot_x - 140)) <  60 then 'Right Wing 3'
                when degrees(atan2(s.shot_y, s.shot_x - 140)) < 120 then 'Top of Key 3'
                else 'Left Wing 3'
            end
    end as court_zone,

    s.action_text

from shots s
join {{ ref('stg_games') }} g using (game_id)
left join {{ ref('stg_player_box') }} b
       on b.game_id = s.game_id and b.person_id = s.person_id
