-- Forfeits and any other game FIBA has a result for but no box score, one row
-- per game, with the competition name the rest of the models key on.
--
-- The raw row only knows the event slug, so the name comes from any scraped
-- game in the same event: every game URL carries the slug. A forfeit in an
-- event with no scraped games at all has nothing to join to and drops out,
-- which is fine: there is no tournament on the site for it to belong to.

with results as (

    select * from {{ source('raw', 'result_only_games') }}

),

events as (

    select distinct
        split_part(source_url, '/', 6) as event_slug,
        competition
    from {{ ref('stg_games') }}

)

select
    r.game_id,
    e.competition,
    r.event_slug,
    r.round_name,
    r.game_date,
    r.home_code,
    r.away_code,
    r.home_score,
    r.away_score
from results r
join events e using (event_slug)
