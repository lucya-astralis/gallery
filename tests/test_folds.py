"""Long rankings fold instead of summing their tail into one row.

/stats used to end the camera, album and tag charts in a single "+14 more"
row that named none of the fourteen. The rows stay now, marked `extra`, and
the bars macro puts them in a native <details> -- a disclosure that works
without a script under the strict CSP.
"""

from collections import Counter

from aperture import stats
from aperture.gallery import context


def _bars():
    return context.templates.env.get_template("_charts.html").module


def test_rows_past_the_limit_are_marked_not_dropped():
    counts = Counter({f"album{i:02d}": 30 - i for i in range(12)})
    rows = stats.album_rows(counts, label_of=str.upper, top=10)
    assert len(rows) == 12
    assert [r["extra"] for r in rows] == [False] * 10 + [True] * 2
    assert rows[-1]["album"] == "album11" and rows[-1]["label"] == "ALBUM11"
    # a folded bar is measured against the whole series, not against the rest
    assert rows[-1]["pct"] == round(19 * 100.0 / 30, 2)


def test_a_short_ranking_folds_nothing():
    rows = stats.album_rows(Counter({"a": 3, "b": 2}), label_of=str)
    assert not any(r["extra"] for r in rows)
    assert "<details" not in str(_bars().album_bars(rows, "+{n} more"))


def test_the_macro_folds_the_tail_into_a_disclosure():
    rows = stats.fold(stats.rows([(f"tag{i}", 20 - i) for i in range(15)]), 12)
    html = str(_bars().bars(rows, "+{n} more"))
    assert html.count('class="ch-bar ') == 15              # every row is rendered
    assert '<details class="ch-more">' in html
    assert "+3 more" in html
    head, tail = html.split('<details class="ch-more">')
    assert "tag11" in head and "tag12" not in head
    assert "tag12" in tail and "tag14" in tail
    assert 'start="13"' in tail                             # numbering carries on


def test_the_stats_page_still_renders_its_charts(client, indexed):
    body = client.get("/stats").text
    assert "Fixture Cam" in body
    assert "+0" not in body
