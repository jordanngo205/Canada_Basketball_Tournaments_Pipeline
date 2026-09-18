-- Field goal attempts counted from the play-by-play have to match the box
-- score, per player per game.
--
-- This is the test that makes the shot charts trustworthy. The coordinates and
-- the box score come from completely different parts of FIBA's payload, so if
-- the play-by-play parse drops or duplicates shots, nothing else would notice —
-- the chart would just be quietly wrong.
--
-- A tolerance of 1 covers the handful of attempts FIBA logs without
-- coordinates, which are excluded from int_shots by design.

with from_pbp as (

    select
        game_id,
        person_id,
        count(*)                                as pbp_fga,
        count(*) filter (where shot_value = 3)  as pbp_fg3a
    from {{ ref('int_shots') }}
    group by game_id, person_id

)

select
    b.game_id,
    b.person_id,
    b.player_name,
    b.fga,
    p.pbp_fga,
    b.fg3a,
    p.pbp_fg3a
from {{ ref('stg_player_box') }} b
join from_pbp p using (game_id, person_id)
where abs(b.fga - p.pbp_fga) > 1
   or abs(b.fg3a - p.pbp_fg3a) > 1
