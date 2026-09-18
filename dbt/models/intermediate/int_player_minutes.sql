-- Minutes played per player per game, reconstructed from substitutions.
--
-- FIBA leaves MP null in the box-score payload, so this is the only way to get
-- minutes at all — and without minutes there are no per-minute rates and no
-- honest way to compare a starter with someone off the bench.
--
-- The method: a starter is on court from 0:00. Every SUBST IN opens a stint,
-- every SUBST OUT closes one, and anyone still on at the final whistle is
-- closed out there. Sum the stints.
--
-- Two things make this harder than it sounds. FIBA does not emit a substitution
-- at the start of a period for players who begin it on court, so a player who
-- sits at the end of Q1 and starts Q2 has an OUT with no matching IN. And the
-- starter flag only covers the opening tip, not the start of Q2/Q3/Q4.
--
-- So rather than trusting the event stream alone, this walks each player's
-- events in order and treats an unmatched OUT as "was on court since the start
-- of this period". That recovers the common case without inventing stints.
--
-- ACCURACY, measured against the 200 team-minutes a 40-minute game must
-- produce, over 336 team-games:
--
--     within 2 min   335  (99.7%)
--     within 10 min  336  (100%)
--     mean abs error  0.12 min  (7 seconds)
--
-- Close enough to treat as real. They're still called est_ because they are
-- reconstructed rather than reported, and a future tournament with messier
-- events could drift — assert_team_minutes_reconcile is what would catch it.

with subs as (

    select
        game_id,
        person_id,
        period_number,
        seconds_elapsed,
        action_order,
        sub_direction
    from {{ ref('stg_pbp') }}
    where action_type = 'subst'
      and person_id is not null
      and seconds_elapsed is not null

),

-- Canonical period boundaries rather than the first and last event seen.
-- Deriving them from the data undercounts whenever a period's first event
-- isn't at 10:00 on the clock, which is most of them.
period_bounds as (

    select distinct
        game_id,
        period_number,
        case when period_number <= 4
             then (period_number - 1) * 600
             else 2400 + (period_number - 5) * 300
        end as period_start,
        case when period_number <= 4
             then period_number * 600
             else 2400 + (period_number - 4) * 300
        end as period_end
    from {{ ref('stg_pbp') }}
    where period_number is not null

),

game_end as (

    select game_id, max(period_end) as final_second
    from period_bounds
    group by game_id

),

-- Pair each event with the one before it for the same player.
sequenced as (

    select
        s.*,
        lag(s.sub_direction) over w   as prev_direction,
        lag(s.seconds_elapsed) over w as prev_second,
        row_number() over w           as event_num
    from subs s
    window w as (partition by s.game_id, s.person_id order by s.action_order)

),

starters as (

    select game_id, person_id
    from {{ ref('stg_player_box') }}
    where is_starter

),

-- One row per stint on court.
stints as (

    -- An OUT closes a stint. Where that stint began depends on what came before.
    --
    -- The obvious version of this — fall back to the start of the OUT's own
    -- period — silently throws away whole quarters. A starter who plays all of
    -- Q1 and comes off in Q2 has their first event in Q2, so they'd be credited
    -- from the start of Q2 and lose the entire first quarter. Across five
    -- starters that's 50 minutes a game, and it was most of why twelve games
    -- came out 30 to 60 minutes short.
    select
        sq.game_id,
        sq.person_id,
        case
            -- Normal case: closes the stint opened by the matching IN.
            when sq.prev_direction = 'IN' then sq.prev_second
            -- No prior event at all. A starter has been on since the tip;
            -- anyone else came on at a period break FIBA didn't record.
            when sq.prev_direction is null and st.person_id is not null then 0
            when sq.prev_direction is null then pb.period_start
            -- Two OUTs in a row means a missing IN between them. The safest
            -- read is that they came back on at the period break.
            else pb.period_start
        end                                   as stint_start,
        sq.seconds_elapsed                    as stint_end
    from sequenced sq
    join period_bounds pb
      on  pb.game_id       = sq.game_id
     and  pb.period_number = sq.period_number
    left join starters st
      on  st.game_id   = sq.game_id
     and  st.person_id = sq.person_id
    where sq.sub_direction = 'OUT'

    union all

    -- A player whose last event is an IN finished the game on court.
    select
        sq.game_id,
        sq.person_id,
        sq.seconds_elapsed as stint_start,
        ge.final_second    as stint_end
    from sequenced sq
    join game_end ge on ge.game_id = sq.game_id
    where sq.sub_direction = 'IN'
      and sq.action_order = (
            select max(s2.action_order) from sequenced s2
            where s2.game_id = sq.game_id and s2.person_id = sq.person_id
          )

    union all

    -- A starter who was never substituted played the whole game.
    select
        st.game_id,
        st.person_id,
        0               as stint_start,
        ge.final_second as stint_end
    from starters st
    join game_end ge on ge.game_id = st.game_id
    where not exists (
        select 1 from subs s where s.game_id = st.game_id and s.person_id = st.person_id
    )

)

select
    game_id,
    person_id,
    count(*)                                         as stints,
    -- Clamp at zero: a malformed pair would otherwise subtract minutes.
    sum(greatest(stint_end - stint_start, 0))        as est_seconds_played,
    round(sum(greatest(stint_end - stint_start, 0)) / 60.0, 1) as est_minutes_played
from stints
group by game_id, person_id
