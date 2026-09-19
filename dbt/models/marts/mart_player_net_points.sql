-- Net points per player per game: plus/minus, split into an offensive and a
-- defensive share.
--
-- Raw plus/minus says a player was on court for a +12 swing but not which end
-- of the floor produced it. The split attributes it by where the team actually
-- gained ground: how far its offensive rating sat above 100, against how far
-- its defensive rating sat below.
--
--     off_delta = ORTG - 100
--     def_delta = 100 - DRTG
--     off_share = |off_delta| / (|off_delta| + |def_delta|)
--
-- A team that won by scoring gives most of each player's plus/minus to
-- off_net; one that won by stopping people gives it to def_net. When neither
-- end moved, it splits evenly rather than dividing by zero.
--
-- It is an attribution, not a measurement. Every player on the floor gets the
-- same split, so it describes the team's shape rather than the individual's.

with box as (

    select
        b.game_id,
        b.person_id,
        b.player_name,
        b.team_id,
        b.side,
        b.is_starter,
        b.plus_minus,
        b.pts, b.reb, b.ast, b.stl, b.blk, b.tov, b.pf, b.fouls_drawn,
        b.fgm, b.fga, b.fg3m, b.ftm,
        g.game_date,
        g.competition,
        case when b.side = 'home' then g.home_code else g.away_code end as team_code
    from {{ ref('stg_player_box') }} b
    join {{ ref('stg_games') }} g using (game_id)
    where b.has_played

),

team_rating as (

    -- The intermediate model carries the counts; the ratings are derived in
    -- mart_team_efficiency. Recomputing here rather than joining the mart
    -- keeps this from depending on a sibling mart.
    select
        game_id,
        team_id,
        100.0 * pts / nullif(possessions, 0)         as ortg,
        100.0 * opp_pts / nullif(opp_possessions, 0) as drtg
    from {{ ref('int_team_game_opponent') }}

),

-- Makes by zone, so the shot-location awards have something to rank on.
zone_makes as (

    select
        game_id,
        person_id,
        count(*) filter (where court_zone = 'At the Rim' and shot_made)               as rim_makes,
        count(*) filter (where court_zone like 'Mid-Range%' and shot_made)            as midrange_makes,
        count(*) filter (where court_zone like '%Corner 3' and shot_made)             as corner3_makes,
        count(*) filter (where court_zone in ('Left Wing 3','Right Wing 3','Top of Key 3')
                          and shot_made)                                              as abovebreak3_makes
    from {{ ref('int_shots') }}
    group by game_id, person_id

),

split as (

    select
        b.*,
        r.ortg,
        r.drtg,
        abs(r.ortg - 100)                                   as off_delta,
        abs(100 - r.drtg)                                   as def_delta,
        abs(r.ortg - 100) + abs(100 - r.drtg)               as total_delta
    from box b
    join team_rating r on r.game_id = b.game_id and r.team_id = b.team_id

)

select
    s.competition,
    s.game_date,
    s.game_id,
    s.person_id,
    s.player_name,
    s.team_code,
    s.is_starter,

    s.plus_minus                                            as net_points,
    round(s.plus_minus * case when s.total_delta > 0
                              then s.off_delta / s.total_delta else 0.5 end, 1) as off_net,
    round(s.plus_minus * case when s.total_delta > 0
                              then s.def_delta / s.total_delta else 0.5 end, 1) as def_net,
    s.ortg,
    s.drtg,

    s.pts, s.reb, s.ast, s.stl, s.blk, s.tov, s.pf, s.fouls_drawn,
    s.fgm, s.fga, s.fg3m, s.ftm,
    round(100.0 * s.fgm / nullif(s.fga, 0), 1)              as fg_pct,
    coalesce(z.rim_makes, 0)                                as rim_makes,
    coalesce(z.midrange_makes, 0)                           as midrange_makes,
    coalesce(z.corner3_makes, 0)                            as corner3_makes,
    -- Both reconstructed from play-by-play; FIBA publishes neither. Putbacks
    -- are observed, fast-break points are a 7-second estimate — see
    -- int_scoring_types for why the two are not held to the same standard.
    coalesce(sc.putback_pts, 0)                             as putback_pts,
    coalesce(sc.fb_pts, 0)                                  as fb_pts,
    coalesce(z.abovebreak3_makes, 0)                        as abovebreak3_makes

from split s
left join zone_makes z on z.game_id = s.game_id and z.person_id = s.person_id
left join {{ ref('int_scoring_types') }} sc
       on sc.game_id = s.game_id and sc.person_id = s.person_id
