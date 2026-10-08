-- Every play-by-play action that moves a team's Game Score, one row each, for
-- the momentum chart under each game.
--
-- The weights are John Hollinger's Game Score, applied action by action rather
-- than to a box-score line, so the chart can show who was playing well when:
--
--     PTS + 0.4 FGM - 0.7 FGA - 0.4 (FTA - FTM) + 0.7 ORB + 0.3 DRB
--         + STL + 0.7 AST + 0.7 BLK - 0.4 PF - TOV
--
-- A single action carries every term it touches. A made two is a point pair,
-- a make and an attempt — 2 + 0.4 - 0.7 = +1.7 — and a missed three is just
-- the attempt, -0.7. A made free throw is +1 and a missed one -0.4.
--
-- Team rebounds and team turnovers (TREB, TTO) are counted too. They are not
-- in anyone's box score, so a box-score Game Score leaves them out, but the
-- ball changing hands is exactly what this chart is meant to show.
--
-- The smoothing — how long an action keeps counting — is left to the page, so
-- it can be tuned without a rebuild. This mart only says what happened, when
-- and how much it was worth.

with actions as (

    select
        p.game_id,
        g.competition,
        p.period_number,
        p.action_seq,
        p.seconds_elapsed,
        p.action_code,
        -- `oId` is the acting team's numeric id; stg_games stores it as T_<id>.
        -- It is present on team actions (TREB, TTO) as well, which a join
        -- through the box score's person ids would miss.
        case 'T_' || p.opponent_id
            when g.home_team_id then g.home_code
            when g.away_team_id then g.away_code
        end                                                    as team_code,
        case 'T_' || p.opponent_id
            when g.home_team_id then 'home'
            when g.away_team_id then 'away'
        end                                                    as side,
        case
            when p.action_code in ('P2', 'P3') and p.shot_made     then p.shot_value + 0.4 - 0.7
            when p.action_code in ('P2', 'P3')                     then -0.7
            when p.action_code = 'FT' and p.shot_made              then 1.0
            when p.action_code = 'FT'                              then -0.4
            when p.action_code in ('REB', 'TREB')
                 and p.action_text like '%offensive rebound%'      then 0.7
            when p.action_code in ('REB', 'TREB')
                 and p.action_text like '%defensive rebound%'      then 0.3
            when p.action_code = 'ST'                              then 1.0
            when p.action_code = 'ASS'                             then 0.7
            when p.action_code = 'BS'                              then 0.7
            when p.action_code = 'FOUL'                            then -0.4
            when p.action_code in ('TO', 'TTO')                    then -1.0
        end                                                    as game_score,
        case when p.shot_made then p.shot_value else 0 end    as points
    from {{ ref('stg_pbp') }} p
    join {{ ref('stg_games') }} g
      on g.game_id = p.game_id
    where p.seconds_elapsed is not null

)

select
    competition,
    game_id,
    period_number,
    action_seq,
    seconds_elapsed,
    action_code,
    team_code,
    side,
    game_score,
    points
from actions
where game_score is not null
  and team_code is not null
