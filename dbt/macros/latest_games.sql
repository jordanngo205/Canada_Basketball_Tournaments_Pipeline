{#
    The one place that decides whether CI's committed fixtures are in scope,
    and the only thing staging models read raw through.

    Why it exists rather than just selecting raw.latest_games:

    tests/seed_fixtures.py writes into the same raw.raw_games the live ingest
    uses, and before the is_fixture flag the two were indistinguishable. Game
    128116 is from the senior Women's World Cup — a tournament this project does
    not follow — so it rolled up as a fifth competition with a two-team
    standings table and twenty-four "tournament leaders", all from one game. The
    dashboard hid it behind `having count(*) > 1`, which is a guess that also
    hides a real tournament on its opening day. A flag on the row is the honest
    version of that filter.

    Why it re-derives "latest" instead of reading the view:

    a game can have both a live row and a fixture row, and the fixture is not
    always the older of the two. Picking the latest row first and filtering
    second would drop a real game whose newest row happens to be the fixture.
    Filtering first and picking the latest of what remains is the correct order,
    and it is the whole reason this is a macro and not a WHERE clause bolted
    onto the view.

    In CI the fixtures ARE the dataset, so the workflow passes
    `--vars '{include_fixtures: true}'` and the filter drops out.
#}
{% macro latest_games() %}
(
    select distinct on (game_id)
           game_id, source_url, fetched_at, payload, is_fixture
    from {{ source('raw', 'raw_games') }}
    {% if not var('include_fixtures', false) %}
    where not is_fixture
    {% endif %}
    order by game_id, fetched_at desc
)
{% endmacro %}
