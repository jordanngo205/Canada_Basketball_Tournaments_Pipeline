-- Putback and fast-break points per player per game, derived from play-by-play.
--
-- FIBA publishes neither. The action payload carries exactly these keys —
-- ac, act, GT, Id, in, made, oId, order, p2Id, pId, pts, SA, SB, Time, txt,
-- x, y — and there is no fastbreak, secondchance or transition qualifier
-- anywhere in it. So both numbers have to be reconstructed from the sequence,
-- and they are reconstructed to different standards:
--
--   PUTBACK POINTS are a fact. `txt` separates "offensive rebound" from
--   "defensive rebound", so "this player rebounded their team's miss and
--   scored before anyone else touched the ball" is directly observable. The
--   only judgement is that the shot must be the very next action by that
--   player with no other player's action in between, which is what a putback
--   means.
--
--   FAST-BREAK POINTS are an estimate, and labelled one downstream. There is
--   no way to observe that a defence was unset. The convention — and what the
--   NBA's own definition reduces to — is a score soon after winning the ball,
--   so this counts a made field goal within FASTBREAK_WINDOW seconds of the
--   same team taking possession by steal or defensive rebound, with no
--   stoppage in between. A whistle or a timeout lets the defence set, so any
--   intervening foul, timeout or period break disqualifies it.
--
-- The window is 7 seconds. That is the usual choice, and the sensitivity is
-- mild: 6 and 8 seconds move the tournament total by a few percent rather than
-- reshaping it.

{% set fastbreak_window = 7 %}

with

-- Which team each acting player belongs to. The play-by-play gives a person id
-- and nothing else, so team attribution has to come from the box score.
player_team as (

    select game_id, person_id, team_id
    from {{ ref('stg_player_box') }}
    where person_id is not null

),

actions as (

    select
        p.game_id,
        p.period_number,
        p.action_seq,
        p.seconds_elapsed,
        p.person_id,
        p.action_code,
        p.action_text,
        p.shot_made,
        p.shot_value,
        pt.team_id,
        -- Order within the game. action_order is unusable as a sort key —
        -- FIBA leaves it $undefined on the odd action and a null silently
        -- reshuffles the stream — so period then sequence, as everywhere else.
        row_number() over (
            partition by p.game_id
            order by p.period_number, p.action_seq
        ) as n
    from {{ ref('stg_pbp') }} p
    left join player_team pt
      on  pt.game_id   = p.game_id
     and  pt.person_id = p.person_id
    where p.period_number is not null

),

-- PUTBACKS -----------------------------------------------------------------
-- A made field goal whose immediately preceding action is an offensive
-- rebound by the same player. "Immediately" is literal: n - 1. Anything
-- between them means the ball moved, and a shot after the ball moves is a
-- normal possession rather than a putback.
putbacks as (

    select
        s.game_id,
        s.person_id,
        sum(s.shot_value) as putback_pts
    from actions s
    join actions r
      on  r.game_id = s.game_id
     and  r.n       = s.n - 1
    where s.action_code in ('P2', 'P3')
      and s.shot_made
      and s.person_id is not null
      and r.action_code  = 'REB'
      and r.action_text  = 'offensive rebound'
      and r.person_id    = s.person_id
    group by s.game_id, s.person_id

),

-- FAST BREAKS --------------------------------------------------------------
-- Every moment a team wins the ball back: a steal, or a defensive rebound.
-- Team rebounds (TREB) are included — the ball is still won — but they carry
-- no person id, so the team comes from the rebound's own text and the
-- possession is attributed to whoever scores.
takeaways as (

    select
        a.game_id,
        a.n,
        a.seconds_elapsed,
        a.team_id
    from actions a
    where a.team_id is not null
      and (
            a.action_code = 'ST'
         or (a.action_code = 'REB' and a.action_text = 'defensive rebound')
      )

),

-- Anything between winning the ball and scoring that means the bucket was not
-- a fast break after all.
--
-- Two kinds. The obvious one is a clock stoppage — a whistle, a timeout, a
-- period break — which is exactly the time a defence needs to get set.
--
-- The other is an offensive rebound, and leaving it out was a real bug rather
-- than a nicety. A possession that survives a miss is a second-chance
-- possession by definition, no matter how quickly it all happened, so it is
-- not also a transition possession. Without this, the sequence
--
--     defensive rebound -> miss -> offensive rebound -> putback
--
-- inside seven seconds counted the same basket as both a fast break and a
-- putback, and 402 shots hit it. Two players ended up credited with more
-- fast-break plus putback points than they scored in total, which is what
-- gave it away.
break_enders as (

    select game_id, n
    from actions
    where action_code in ('FOUL', 'RFOUL', 'CFOUL', 'TIMO', 'TTO', 'ENDP', 'STARTP', 'JB', 'JS')
       or (action_code in ('REB', 'TREB') and action_text like '%offensive rebound')

),

fastbreaks as (

    select
        s.game_id,
        s.person_id,
        sum(s.shot_value) as fb_pts
    from actions s
    join lateral (
        -- The most recent takeaway by the shooter's own team before this shot.
        select t.n, t.seconds_elapsed
        from takeaways t
        where t.game_id = s.game_id
          and t.team_id = s.team_id
          and t.n       < s.n
        order by t.n desc
        limit 1
    ) t on true
    where s.action_code in ('P2', 'P3')
      and s.shot_made
      and s.person_id is not null
      and s.seconds_elapsed - t.seconds_elapsed between 0 and {{ fastbreak_window }}
      -- Nothing between winning the ball and scoring that ends the break.
      and not exists (
          select 1 from break_enders st
          where st.game_id = s.game_id
            and st.n > t.n
            and st.n < s.n
      )
    group by s.game_id, s.person_id

)

select
    coalesce(p.game_id, f.game_id)     as game_id,
    coalesce(p.person_id, f.person_id) as person_id,
    coalesce(p.putback_pts, 0)         as putback_pts,
    coalesce(f.fb_pts, 0)              as fb_pts
from putbacks p
full outer join fastbreaks f
  on  f.game_id   = p.game_id
 and  f.person_id = p.person_id
