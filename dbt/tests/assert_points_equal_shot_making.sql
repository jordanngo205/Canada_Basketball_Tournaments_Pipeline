-- Basketball arithmetic that cannot be false: points are two per made
-- two-pointer, three per made three, one per made free throw.
--
-- Unlike a threshold someone guessed at, a failure here is unambiguously a
-- parsing bug — most likely a stat key mapped to the wrong column.

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
