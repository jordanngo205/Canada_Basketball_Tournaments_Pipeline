-- Arithmetic that can't be false. Two per two, three per three, one per FT.
--
-- Nothing to argue about here — a failure means a stat key got mapped to the
-- wrong column.

select
    game_id,
    person_id,
    player_name,
    pts,
    fg2m,
    fg3m,
    ftm,
    (2 * fg2m + 3 * fg3m + ftm) as implied_pts
from {{ ref('stg_player_box') }}
where pts != (2 * fg2m + 3 * fg3m + ftm)
