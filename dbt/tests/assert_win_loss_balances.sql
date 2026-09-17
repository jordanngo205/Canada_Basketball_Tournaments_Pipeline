-- Every game has exactly one winner and one loser, so across a competition the
-- wins and losses must balance. If they do not, a game was ingested for one
-- team but not the other — the kind of half-loaded state that makes a
-- standings table quietly wrong.
--
-- Ties are impossible in FIBA: a drawn game goes to overtime.

select
    competition,
    sum(wins)   as total_wins,
    sum(losses) as total_losses
from {{ ref('mart_standings') }}
group by competition
having sum(wins) != sum(losses)
