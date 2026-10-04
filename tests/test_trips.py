"""Trip itinerary: which leg a day is filed under, and side trips riding
along on their base's chip — a new region (Sendai) and a revisit of a leg
already done (Sapporo)."""

from aperture import trips


TRIP = trips.TRIPS["japan_2026"]


def test_day_outside_any_side_trip_is_the_leg():
    assert trips.trip_stop_on(TRIP, "2026-10-01", "en") == "Kanto"
    assert trips.trip_stop_on(TRIP, "2026-08-20", "en") == "Hokkaido"


def test_side_trip_counts_from_arrival_not_departure():
    # left Tokyo at 23:00 the evening before: that evening stays plain Kanto
    assert trips.trip_stop_on(TRIP, "2026-10-02", "en") == "Kanto"
    assert trips.trip_stop_on(TRIP, "2026-10-03", "en") == "Kanto · ↗ Sendai"
    assert trips.trip_stop_on(TRIP, "2026-10-03", "jp") == "関東 · ↗ 仙台"


def test_revisit_rides_on_the_base_leg_every_day_it_covers():
    for day in ("2026-12-18", "2026-12-19", "2026-12-20"):
        assert trips.trip_stop_on(TRIP, day, "en") == "Kanto · ↗ Sapporo"
    assert trips.trip_stop_on(TRIP, "2026-12-21", "en") == "Kanto"


def test_trip_ends_at_the_flight_home():
    assert TRIP["stops"][-1]["end"] == "2027-01-01T10:45:00"
    assert trips.trip_stop_on(TRIP, "2027-01-01", "en") == "Kanto"
    assert trips.trip_stop_on(TRIP, "2027-01-02", "en") is None


def test_side_trips_start_from_a_leg_and_end_after_they_start():
    legs = {s["city"] for s in TRIP["stops"]}
    for side in TRIP["side_trips"]:
        assert side["from"] in legs
        assert side["start"] <= side.get("arrive", side["start"]) < side["end"]
