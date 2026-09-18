-- A team's minutes have to add up to 5 players x the length of the game: 200
-- for regulation, plus 25 for each overtime period.
--
-- Minutes are RECONSTRUCTED from substitution events rather than read from the
-- box score, since FIBA leaves MP null. The reconstruction lands within 7
-- seconds a game on average, with a worst case of 4.9 across 336 team-games.
-- 6 minutes sits above that and well below the 30-60 minute drift the old
-- starter bug produced, so it catches a real regression without failing on a
-- stray unmatched event.

with team_minutes as (

    select
        m.game_id,
        b.team_id,
        sum(m.est_minutes_played) as team_minutes
    from {{ ref('int_player_minutes') }} m
    join {{ ref('stg_player_box') }} b using (game_id, person_id)
    group by m.game_id, b.team_id

),

expected as (

    select
        game_id,
        200 + greatest(max(period_number) - 4, 0) * 25 as expected_minutes
    from {{ ref('stg_pbp') }}
    group by game_id

)

select
    t.game_id,
    t.team_id,
    t.team_minutes,
    e.expected_minutes,
    round(t.team_minutes - e.expected_minutes, 1) as drift
from team_minutes t
join expected e using (game_id)
where abs(t.team_minutes - e.expected_minutes) > 6
