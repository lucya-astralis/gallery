"""Trip dashboards.

The configured trips, which album belongs to which trip and stop, and the
server-side weather proxy the dashboard reads.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

from . import albums, brand, i18n


# ----- trip dashboard ---------------------------------------------------
# An optional "trip" overlay (a live flight countdown + an itinerary
# timeline with a "you are here" marker) rendered at the top of one album.
# Config is keyed by the album's lower-cased path (e.g. `japan_2026`).
# All dates are wall-clock; the live
# countdown and current-stop highlight are computed client-side (initTrip
# in app.js) against the viewer's own clock — so it reads correctly both
# from home before the flight and on the ground once the trip is underway.
TRIPS: dict[str, dict] = {
    "japan_2026": {
        "title": "Japan 2026",
        "jp": "日本",
        # flight out (local wall-clock). 12:00 = noon departure.
        "depart": "2026-08-09T12:00:00",
        # A stop is a REGION, not a single city: the trip stays put in one
        # part of the country per leg, and its album holds everything shot
        # there. lat/lon stay the region's base city (Kansai -> Osaka,
        # Hokkaido -> Sapporo, Kanto -> Tokyo) — they feed the
        # /api/trip-weather proxy (see below) and the route map's dot, both
        # of which need one point. The map's highlight is the whole region
        # (tools/generate_trip_map.py). A leg card carries no album icon:
        # the region album's `icon = …` shows on its card, hero and
        # breadcrumb, but the timeline is the trip's, not the albums'.
        "stops": [
            # A stop's end / the next stop's start is the domestic flight's
            # departure (JST wall-clock), so the countdown runs to the gate
            # rather than to midnight of the travel day.
            {"city": "Kansai",   "jp": "関西",   "album": "kansai",   "start": "2026-08-10",          "end": "2026-08-16T14:30:00", "lat": 34.6937, "lon": 135.5023},
            {"city": "Hokkaido", "jp": "北海道", "album": "hokkaido", "start": "2026-08-16T14:30:00", "end": "2026-09-16T10:30:00", "lat": 43.0618, "lon": 141.3545},
            {"city": "Kanto",    "jp": "関東",   "album": "kanto",    "start": "2026-09-16T10:30:00", "end": "2027-01-02",          "lat": 35.6895, "lon": 139.6917},
        ],
    },
}


def trip_for_album(album: str, lang: str = i18n.DEFAULT_LANG) -> dict | None:
    """Render-ready trip dashboard for `album`, or None when the album has
    no configured trip. Matched on the lower-cased album path. Each stop is
    wired to its sub-album — cover + photo count + link — so the timeline
    doubles as navigation into the region galleries (empty folders stay
    unlinked).
    Human-readable dates are localized; app.js re-renders them client-side
    in the same language (read from <html lang>)."""
    key = album.lower()
    cfg = TRIPS.get(key)
    if not cfg:
        return None
    stops = []
    for s in cfg["stops"]:
        sub = f"{album}/{s['album']}" if s.get("album") else None
        card = albums.album_card(sub) if sub else None
        count = card["count"] if card else 0
        stops.append({
            "city": s["city"],
            "jp": s.get("jp", ""),
            "start": s["start"],
            "end": s["end"],
            "start_h": i18n.fmt_date(lang, s["start"]),
            "end_h": i18n.fmt_date(lang, s["end"]),
            "href": f"/album/{sub}" if count else None,
            "cover": card["cover"] if card else None,
            "count": count,
        })
    return {
        "key": key,  # TRIPS key, echoed as data-trip-key for /api/trip-weather
        "title": cfg["title"],
        "jp": cfg.get("jp", ""),
        "depart": cfg["depart"],
        "depart_h": i18n.fmt_date(lang, cfg["depart"]),
        "stops": stops,
    }


# ----- trip weather (server-side proxy) ----------------------------------
# Current conditions per trip stop, fetched from the Open-Meteo forecast API
# and re-served same-origin. Proxying is what keeps this consent-free and
# CSP-clean: the visitor's browser only ever talks to this origin (no
# third-party request, no cookies, nothing stored on the device — GDPR/
# ePrivacy don't require a banner for it), and connect-src 'self' stays.
# Upstream sees only this server's IP plus fixed base-city coordinates.
# Open-Meteo is keyless and cookie-free; data is CC BY 4.0 — attributed in
# the widget tooltip (see initTrip) and README. One upstream call covers
# all stops; results are cached for WEATHER_TTL so page-view bursts cost
# at most one fetch, and the last good payload is served on upstream errors.
WEATHER_TTL = 900

  # seconds; weather for a dashboard doesn't need more
# How long a failed upstream call is remembered. Without it an outage cost one
# blocking 8s request per page view: only a SUCCESS refreshes the TTL, so every
# caller went past the cache check, queued on the lock and waited out the
# timeout again. Short enough that the widget comes back on its own once
# Open-Meteo does.
WEATHER_RETRY_AFTER = 60
weather_lock = threading.Lock()
weather_cache: dict[str, tuple[float, dict]] = {}

  # trip key -> (fetched_at, payload)
weather_failed_at: dict[str, float] = {}


  # trip key -> when the last fetch blew up
def fetch_trip_weather(cfg: dict) -> dict:
    """One Open-Meteo request for every stop of `cfg` (multi-location call).
    Returns the trimmed same-origin payload; raises on network trouble —
    the endpoint decides between stale-cache and 502."""
    stops = [s for s in cfg["stops"] if "lat" in s and "lon" in s]
    if not stops:
        return {"updated": int(time.time()), "stops": []}
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=" + ",".join(str(s["lat"]) for s in stops) +
        "&longitude=" + ",".join(str(s["lon"]) for s in stops) +
        "&current=temperature_2m,weather_code,is_day"
        # today's envelope, so the widget can show a hi/lo next to "now"
        "&daily=temperature_2m_max,temperature_2m_min&forecast_days=1"
        "&timezone=Asia%2FTokyo"
    )
    req = urllib.request.Request(url, headers={"User-Agent": brand.USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.load(resp)
    if isinstance(payload, dict):  # single-location responses aren't wrapped
        payload = [payload]
    def _first(daily: dict, key: str):
        """Today's value from a `daily` block — absent/short arrays are fine
        (hi/lo is a bonus line in the widget, never a hard requirement)."""
        vals = (daily or {}).get(key) or []
        try:
            return round(float(vals[0]))
        except (IndexError, TypeError, ValueError):
            return None

    out = []
    for s, loc in zip(stops, payload):
        cur = (loc or {}).get("current") or {}
        temp, code = cur.get("temperature_2m"), cur.get("weather_code")
        if temp is None or code is None:
            continue
        daily = (loc or {}).get("daily") or {}
        out.append({
            "city": s["city"],  # English stop key, matches data-city / data-stop-wx lookup
            "temp": float(temp),
            "code": int(code),
            "is_day": int(cur.get("is_day") or 0),
            "hi": _first(daily, "temperature_2m_max"),
            "lo": _first(daily, "temperature_2m_min"),
        })
    return {"updated": int(time.time()), "stops": out}


def ancestor_trip(album: str, lang: str = i18n.DEFAULT_LANG) -> dict | None:
    """The trip config of `album` or of its nearest configured ancestor. The
    trip DASHBOARD only ever renders on the album that configures it, but the
    day sections of a sub-album (japan_2026/hokkaido/sapporo) should still
    count trip days and name the leg — so they look the trip up upwards."""
    parts = album.split("/")
    for i in range(len(parts), 0, -1):
        trip = trip_for_album("/".join(parts[:i]), lang)
        if trip:
            return trip
    return None


def trip_stop_on(trip: dict | None, day: str, lang: str) -> str | None:
    """Name of the trip stop a given day (YYYY-MM-DD) falls into, for the day
    headers of a trip album ("14 AUG · KANSAI"). Start-inclusive, so a travel
    day is filed under the region you arrive in — the same rule initTrip()
    uses for the live "you are here" marker. None outside the itinerary, and
    for albums without a trip."""
    if not trip:
        return None
    hit = None
    for s in trip.get("stops") or []:
        start, end = (s.get("start") or "")[:10], (s.get("end") or "")[:10]
        if start and start <= day and (not end or day <= end):
            hit = s
    if not hit:
        return None
    return (hit.get("jp") or hit["city"]) if lang == "jp" else hit["city"]
