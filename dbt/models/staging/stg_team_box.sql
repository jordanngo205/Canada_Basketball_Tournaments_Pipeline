-- One row per team per game, using FIBA's own totals.
--
-- Kept separate from summing the player rows deliberately — having both is
-- what lets a test reconcile them and catch a roster row that didn't parse.

with games as (

    select
        game_id,
        payload -> 'gameDetails' -> 'c' as teams
    from {{ source('raw', 'latest_games') }}

),

sides as (

    select
        g.game_id,
        case when t.idx = 1 then 'home' else 'away' end as side,
        t.team ->> 'Id'      as team_id,
        (t.team ->> 'Score')::int as final_score,
        t.team -> 'Stats'    as st
    from games g,
         lateral jsonb_array_elements(g.teams) with ordinality as t(team, idx)

)

select
    game_id,
    team_id,
    side,
    final_score,

    (st ->> 'PTS')::int  as pts,
    (st ->> 'REB')::int  as reb,
    (st ->> 'OR')::int   as oreb,
    (st ->> 'DR')::int   as dreb,
    (st ->> 'AS')::int   as ast,
    (st ->> 'ST')::int   as stl,
    (st ->> 'BS')::int   as blk,
    (st ->> 'TO')::int   as tov,
    (st ->> 'PF')::int   as pf,

    (st ->> 'FG2M')::int as fg2m,
    (st ->> 'FG2A')::int as fg2a,
    (st ->> 'FG3M')::int as fg3m,
    (st ->> 'FG3A')::int as fg3a,
    (st ->> 'FGM')::int  as fgm,
    (st ->> 'FGA')::int  as fga,
    (st ->> 'FTM')::int  as ftm,
    (st ->> 'FTA')::int  as fta,
    (st ->> 'FGIM')::int as paint_fgm,
    (st ->> 'FGIA')::int as paint_fga

from sides
