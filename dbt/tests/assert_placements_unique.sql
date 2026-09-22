-- Two teams can't both finish third.
--
-- A duplicate means two sources disagree — a hand-stated placement colliding
-- with one derived from a placement game — or a round name was parsed wrong.

select competition, placement, count(*) as teams
from {{ ref('mart_final_placement') }}
where placement is not null
group by competition, placement
having count(*) > 1
