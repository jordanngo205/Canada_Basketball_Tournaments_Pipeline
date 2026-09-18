-- One row per game. The header stuff, no stats.
--
-- Watch out: gameDetails.teamA and teamB are NOT the team records. They're RSC
-- pointers — literal strings like '$1c:props:gameDetails:c:0:Stats'. The real
-- records are at gameDetails.c[0] (home) and c[1] (away). Read `c`, always.

with source as (

    select
        game_id,
        source_url,
        fetched_at,
        payload
    from {{ source('raw', 'latest_games') }}

),

unpacked as (

    select
        game_id,
        source_url,
        fetched_at,
        payload -> 'game'                          as game,
        payload -> 'gameDetails'                   as details,
        payload -> 'gameDetails' -> 'c' -> 0       as home,
        payload -> 'gameDetails' -> 'c' -> 1       as away
    from source

)

select
    game_id,

    -- Absent values come through as the string '$undefined', not null.
    nullif(game ->> 'gameDateTime', '$undefined')::timestamptz   as tipoff_at,
    (nullif(game ->> 'gameDateTime', '$undefined'))::date        as game_date,

    game -> 'teamA' ->> 'officialName'                           as home_team,
    game -> 'teamA' ->> 'code'                                   as home_code,
    home ->> 'Id'                                                as home_team_id,
    (home ->> 'Score')::int                                      as home_score,

    game -> 'teamB' ->> 'officialName'                           as away_team,
    game -> 'teamB' ->> 'code'                                   as away_code,
    away ->> 'Id'                                                as away_team_id,
    (away ->> 'Score')::int                                      as away_score,

    nullif(game -> 'competition' ->> 'officialName', '$undefined') as competition,
    nullif(game -> 'competition' ->> 'fibaZone', '$undefined')     as fiba_zone,
    nullif(game -> 'round' ->> 'roundName', '$undefined')          as round_name,
    nullif(game ->> 'hostCity', '$undefined')                      as city,
    nullif(game ->> 'hostCountry', '$undefined')                   as country,

    -- Group phase puts a letter here. Knockout games reuse the field for a
    -- bracket slot number, so only keep it if it's actually a letter.
    case
        when nullif(game ->> 'groupPairingCode', '$undefined') ~ '^[A-Za-z]+$'
        then upper(game ->> 'groupPairingCode')
    end                                                          as group_code,

    jsonb_array_length(details -> 'quartersScores')              as periods_played,
    source_url,
    fetched_at

from unpacked
