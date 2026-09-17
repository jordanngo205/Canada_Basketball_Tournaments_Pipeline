-- One row per player per game — the grain the whole scouting report is built on.
--
-- Stats sit at gameDetails.c[side].Children[*].Stats. The roster (names,
-- positions, shirt numbers) sits in a separate top-level array and joins on a
-- prefixed id: the stat block says 'P_342479', the roster says 342479.

with games as (

    select
        game_id,
        payload,
        payload -> 'gameDetails' -> 'c' as teams
    from {{ source('raw', 'latest_games') }}

),

sides as (

    -- c[0] is the home team and c[1] the away team, so the array index is the
    -- side. `with ordinality` makes that positional fact explicit rather than
    -- something a reader has to know.
    select
        g.game_id,
        g.payload,
        case when t.idx = 1 then 'home' else 'away' end as side,
        case when t.idx = 1 then 'playersTeamA' else 'playersTeamB' end as roster_key,
        t.team
    from games g,
         lateral jsonb_array_elements(g.teams) with ordinality as t(team, idx)

),

stat_lines as (

    select
        s.game_id,
        s.side,
        s.payload,
        s.roster_key,
        s.team ->> 'Id'                              as team_id,
        replace(p.player ->> 'Id', 'P_', '')         as person_id,
        p.player -> 'Stats'                          as st
    from sides s,
         lateral jsonb_array_elements(s.team -> 'Children') as p(player)

),

roster as (

    select
        s.game_id,
        s.side,
        r.person ->> 'personId'                                       as person_id,
        trim(
            coalesce(nullif(r.person ->> 'firstName', '$undefined'), '') || ' ' ||
            coalesce(nullif(r.person ->> 'lastName',  '$undefined'), '')
        )                                                             as player_name,
        nullif(r.person ->> 'position', '$undefined')                 as position,
        nullif(r.person ->> 'uniformNumber', '$undefined')            as jersey,
        coalesce((r.person ->> 'isCaptain')::boolean, false)          as is_captain
    from (select distinct game_id, side, payload, roster_key from stat_lines) s,
         lateral jsonb_array_elements(s.payload -> s.roster_key) as r(person)

)

select
    sl.game_id,
    sl.person_id,
    sl.team_id,
    sl.side,
    r.player_name,
    r.position,
    r.jersey,
    r.is_captain,

    coalesce((sl.st ->> 'Starter')::boolean, false)   as is_starter,
    coalesce((sl.st ->> 'HasPlayed')::boolean, false) as has_played,

    (sl.st ->> 'PTS')::int    as pts,
    (sl.st ->> 'REB')::int    as reb,
    (sl.st ->> 'OR')::int     as oreb,
    (sl.st ->> 'DR')::int     as dreb,
    (sl.st ->> 'AS')::int     as ast,
    (sl.st ->> 'ST')::int     as stl,
    (sl.st ->> 'BS')::int     as blk,
    (sl.st ->> 'TO')::int     as tov,
    (sl.st ->> 'PF')::int     as pf,
    (sl.st ->> 'FD')::int     as fouls_drawn,

    (sl.st ->> 'FG2M')::int   as fg2m,
    (sl.st ->> 'FG2A')::int   as fg2a,
    (sl.st ->> 'FG3M')::int   as fg3m,
    (sl.st ->> 'FG3A')::int   as fg3a,
    (sl.st ->> 'FGM')::int    as fgm,
    (sl.st ->> 'FGA')::int    as fga,
    (sl.st ->> 'FTM')::int    as ftm,
    (sl.st ->> 'FTA')::int    as fta,

    -- Points in the paint, as FIBA counts them.
    (sl.st ->> 'FGIM')::int   as paint_fgm,
    (sl.st ->> 'FGIA')::int   as paint_fga,

    (sl.st ->> 'EFF')::int    as efficiency,
    (sl.st ->> 'PM')::int     as plus_minus

from stat_lines sl
left join roster r
       on r.game_id  = sl.game_id
      and r.side     = sl.side
      and r.person_id = sl.person_id
