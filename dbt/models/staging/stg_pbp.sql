-- Every play-by-play action, one row each, flattened out of the nested JSON.
--
-- The nesting is two deep: playByPlay.items is keyed by period, and each period
-- has its own items list. Actions carry a clock that COUNTS DOWN within the
-- period, so elapsed time has to be derived rather than read.
--
-- Shot coordinates are on this table too — x and y are percentages of the
-- court, which is what makes shot charts possible without any extra source.

with source as (

    select
        game_id,
        payload -> 'playByPlay' -> 'items' as periods
    from {{ source('raw', 'latest_games') }}

),

flattened as (

    select
        s.game_id,
        per.key                          as period,
        (per.value ->> 'name')           as period_name,
        act.ordinality                   as action_seq,
        act.a                            as action
    from source s,
         lateral jsonb_each(s.periods) as per(key, value),
         lateral jsonb_array_elements(per.value -> 'items') with ordinality as act(a, ordinality)

),

parsed as (

select
    game_id,
    period,

    -- Q1..Q4 then OT1.. — turn that into a sortable number. FIBA plays four
    -- 10-minute quarters, so overtime starts at period 5.
    case
        when period ~ '^Q[0-9]+$'  then substring(period from 2)::int
        when period ~ '^OT[0-9]+$' then 4 + substring(period from 3)::int
    end                                                   as period_number,

    action_seq,
    (action ->> 'order')::bigint                          as action_order,
    nullif(action ->> 'act', '$undefined')                as action_type,
    nullif(action ->> 'ac', '$undefined')                 as action_code,
    nullif(action ->> 'txt', '$undefined')                as action_text,
    nullif(action ->> 'pId', '$undefined')                as person_id,
    nullif(action ->> 'oId', '$undefined')                as opponent_id,
    nullif(action ->> 'in', '$undefined')                 as sub_direction,

    (action ->> 'SA')::int                                as score_home,
    (action ->> 'SB')::int                                as score_away,

    nullif(action ->> 'Time', '$undefined')               as clock,

    -- The clock counts down inside a period. Seconds elapsed in the game is
    -- what everything downstream actually wants, so convert once here.
    case
        when action ->> 'Time' ~ '^[0-9]+:[0-9]+$' then
            (case
                when period ~ '^Q[0-9]+$'  then (substring(period from 2)::int - 1) * 600
                when period ~ '^OT[0-9]+$' then 2400 + (substring(period from 3)::int - 1) * 300
             end)
            + (case when period ~ '^OT' then 300 else 600 end)
            - (split_part(action ->> 'Time', ':', 1)::int * 60
               + split_part(action ->> 'Time', ':', 2)::int)
    end                                                   as seconds_elapsed,

    -- Shots are act='shot' with ac in (P2, P3, FT). Free throws carry x=y=0
    -- rather than null, so they have to be excluded explicitly or every chart
    -- gets a pile of phantom shots in the corner.
    --
    -- The coordinate space is undocumented and had to be derived from the data:
    -- x runs 0-280 ACROSS the court (centre ~140) and y is DISTANCE FROM THE
    -- BASKET, not court length. Layups sit at a median y of 35 and threes at
    -- 131, both centred on x~140 — so both teams' shots are mapped onto a
    -- single half court rather than their own end. Units are not metres.
    case when action ->> 'ac' in ('P2', 'P3')
         then (action ->> 'x')::numeric end               as shot_x,
    case when action ->> 'ac' in ('P2', 'P3')
         then (action ->> 'y')::numeric end               as shot_y,
    case when action ->> 'ac' in ('P2', 'P3', 'FT')
         then (action ->> 'made')::boolean end            as shot_made,
    case when action ->> 'ac' in ('P2', 'P3', 'FT')
         then (action ->> 'pts')::int end                 as shot_value

from flattened

)

-- Filtered out here rather than in `parsed`: Postgres won't let a WHERE clause
-- reference a select alias, and repeating the whole period expression twice is
-- worse than one more CTE.
select * from parsed
where period_number is not null
