-- One winner per award per day of a tournament.
--
-- Built as a union of ranked selects rather than a lateral over a definitions
-- table, because each award ranks a different column in a different direction
-- and some carry their own eligibility rule. Spelled out, it stays readable
-- and a new award is four lines.
--
-- Ties break on the second column named in each rank, so the same two players
-- don't swap places between runs. Awards nobody qualified for emit no row,
-- which the dashboard renders as "not awarded" rather than inventing a winner.

with base as (

    select * from {{ ref('mart_player_net_points') }}

),

ranked as (

    select 'MVP' as award, '🏆' as emoji, 'Highest net points' as blurb, 1 as sort_order,
           competition, game_date, game_id, person_id, player_name, team_code,
           net_points::numeric as stat_value, net_points::text as stat_label,
           row_number() over (partition by competition, game_date order by net_points desc, pts desc) as rn
    from base

    union all
    select 'Heater', '🔥', 'Most of that swing came at the offensive end', 2,
           competition, game_date, game_id, person_id, player_name, team_code,
           off_net, off_net::text,
           row_number() over (partition by competition, game_date order by off_net desc, pts desc)
    from base

    union all
    select 'Stopper', '🛡️', 'Most of that swing came at the defensive end', 3,
           competition, game_date, game_id, person_id, player_name, team_code,
           def_net, def_net::text,
           row_number() over (partition by competition, game_date order by def_net desc, stl desc)
    from base

    union all
    select 'Spark Plug', '⚡', 'Best net points off the bench', 4,
           competition, game_date, game_id, person_id, player_name, team_code,
           net_points::numeric, net_points::text,
           row_number() over (partition by competition, game_date order by net_points desc, pts desc)
    from base where not is_starter

    union all
    select 'Juggernaut', '🚂', 'Most makes at the rim', 5,
           competition, game_date, game_id, person_id, player_name, team_code,
           rim_makes::numeric, rim_makes::text,
           row_number() over (partition by competition, game_date order by rim_makes desc, pts desc)
    from base where rim_makes > 0

    union all
    select 'Corner Pocket', '📐', 'Most corner threes', 6,
           competition, game_date, game_id, person_id, player_name, team_code,
           corner3_makes::numeric, corner3_makes::text,
           row_number() over (partition by competition, game_date order by corner3_makes desc, pts desc)
    from base where corner3_makes > 0

    union all
    select 'Rain Maker', '🌧️', 'Most threes from above the break', 7,
           competition, game_date, game_id, person_id, player_name, team_code,
           abovebreak3_makes::numeric, abovebreak3_makes::text,
           row_number() over (partition by competition, game_date order by abovebreak3_makes desc, pts desc)
    from base where abovebreak3_makes > 0

    union all
    select 'Surgical', '🔬', 'Most mid-range makes', 8,
           competition, game_date, game_id, person_id, player_name, team_code,
           midrange_makes::numeric, midrange_makes::text,
           row_number() over (partition by competition, game_date order by midrange_makes desc, pts desc)
    from base where midrange_makes > 0

    union all
    select 'Glass Cleaner', '🪟', 'Most rebounds', 9,
           competition, game_date, game_id, person_id, player_name, team_code,
           reb::numeric, reb::text,
           row_number() over (partition by competition, game_date order by reb desc, pts desc)
    from base where reb > 0

    union all
    select 'Facilitator', '🎁', 'Most assists', 10,
           competition, game_date, game_id, person_id, player_name, team_code,
           ast::numeric, ast::text,
           row_number() over (partition by competition, game_date order by ast desc, pts desc)
    from base where ast > 0

    union all
    select 'Ball Hawk', '🦅', 'Most steals', 11,
           competition, game_date, game_id, person_id, player_name, team_code,
           stl::numeric, stl::text,
           row_number() over (partition by competition, game_date order by stl desc, reb desc)
    from base where stl > 0

    union all
    select 'Rim Protector', '🚫', 'Most blocks', 12,
           competition, game_date, game_id, person_id, player_name, team_code,
           blk::numeric, blk::text,
           row_number() over (partition by competition, game_date order by blk desc, reb desc)
    from base where blk > 0

    union all
    select 'Contact Artist', '🎯', 'Most fouls drawn', 13,
           competition, game_date, game_id, person_id, player_name, team_code,
           fouls_drawn::numeric, fouls_drawn::text,
           row_number() over (partition by competition, game_date order by fouls_drawn desc, ftm desc)
    from base where fouls_drawn > 0

    -- The unflattering half. A day's worst shooting night is as much a fact as
    -- its best, and a four-attempt floor keeps a 0-for-1 out of it.
    union all
    select 'Ice Cold', '🧊', 'Lowest FG%, minimum four attempts', 14,
           competition, game_date, game_id, person_id, player_name, team_code,
           fg_pct, fg_pct::text || '%',
           row_number() over (partition by competition, game_date order by fg_pct asc, fga desc)
    from base where fga >= 4

    union all
    select 'Hot Potato', '🥔', 'Most turnovers', 15,
           competition, game_date, game_id, person_id, player_name, team_code,
           tov::numeric, tov::text,
           row_number() over (partition by competition, game_date order by tov desc, pts asc)
    from base where tov > 0

    union all
    select 'LVP', '💀', 'Lowest net points', 16,
           competition, game_date, game_id, person_id, player_name, team_code,
           net_points::numeric, net_points::text,
           row_number() over (partition by competition, game_date order by net_points asc, pts asc)
    from base

)

select
    competition, game_date, award, emoji, blurb, sort_order,
    person_id, player_name, team_code, game_id, stat_value, stat_label
from ranked
where rn = 1
