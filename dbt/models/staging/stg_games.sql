-- One row per game: the header a scouting report needs before any stats.
--
-- Note on the payload's shape. `gameDetails.teamA` and `gameDetails.teamB` are
-- NOT the team records — they are RSC reference pointers, strings that look
-- like '$1c:props:gameDetails:c:0:Stats'. The resolved records live at
-- `gameDetails.c[0]` and `gameDetails.c[1]`, in that order (home, away). Every
-- model here reads `c`, never `teamA`/`teamB`.

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

    -- FIBA serialises absent values as the string '$undefined' rather than
    -- null, so every nullable field needs this guard.
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

    -- A group-phase fixture carries a letter here; knockout games reuse the
    -- field for a bracket slot number, which is not a group and is dropped.
    case
        when nullif(game ->> 'groupPairingCode', '$undefined') ~ '^[A-Za-z]+$'
        then upper(game ->> 'groupPairingCode')
    end                                                          as group_code,

    jsonb_array_length(details -> 'quartersScores')              as periods_played,
    source_url,
    fetched_at

from unpacked
