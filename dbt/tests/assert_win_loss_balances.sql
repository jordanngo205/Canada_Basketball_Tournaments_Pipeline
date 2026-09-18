-- One winner, one loser per game, so wins and losses balance across a comp.
--
-- Out of balance means a game landed for one team and not the other, which is
-- exactly the sort of half-loaded state that makes standings quietly wrong.
-- (No ties to worry about — FIBA plays overtime.)

select
    competition,
    sum(wins)   as total_wins,
    sum(losses) as total_losses
from {{ ref('mart_standings') }}
group by competition
having sum(wins) != sum(losses)
