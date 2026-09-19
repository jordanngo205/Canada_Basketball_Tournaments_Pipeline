-- Fast-break and putback points are subsets of a player's scoring, so together
-- they can never exceed it. Basketball arithmetic that cannot be false: if it
-- fails, the derivation is double-counting, not the data being strange.
--
-- This is exactly how the first version broke. A fast break was defined as a
-- score within seven seconds of winning the ball, and a putback as a score off
-- your own offensive rebound, and nothing stopped one basket satisfying both:
--
--     defensive rebound -> miss -> offensive rebound -> putback, inside 7s
--
-- 402 shots matched. Only two players ended up with more derived points than
-- real ones, which is the point of asserting it here — the bug was tiny at the
-- surface and systematic underneath.

select
    game_id,
    person_id,
    player_name,
    pts,
    fb_pts,
    putback_pts,
    fb_pts + putback_pts - pts as excess
from {{ ref('mart_player_net_points') }}
where fb_pts + putback_pts > pts
