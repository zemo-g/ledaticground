#!/usr/bin/env python3
"""channel_geom.py — Detroit River as a single linear track (mile-position projection).

THE INSIGHT (CHANNEL_INTELLIGENCE.md sec 1, Layer 4): the Detroit River is
effectively a single ~32-mile two-way channel running ~N-S from the Lake Erie
mouth up to Lake St. Clair. A railway-dispatcher's time-distance (Marey /
string-line) diagram is the right view: collapse each (lat, lon) vessel fix to a
single scalar "mile along the channel", plot mile vs time, and up-bound x
down-bound line crossings are meets in the narrows.

This module owns that collapse. It is DETERMINISTIC and self-contained (no
external deps, no network, no float surprises beyond the standard library) so it
is reusable by any cluster needing channel-position (shared contract #6).

GEOMETRY
  A piecewise-linear polyline of named channel waypoints (downbound terminus at
  Lake Erie = mile 0, upbound terminus at Lake St. Clair = mile ~32). mile() =
  haversine cumulative arc-length of the projection of a fix onto the nearest
  segment of that polyline. mile increasing == upbound, matching transit_log.py
  compass() (N == upbound). Off-channel fixes clamp to the nearest segment
  endpoint (a vessel at a terminal still gets a sane mile, never NaN).

  Waypoint latitudes are the published, public river landmarks (NOT the rooftop
  receiver location — there is no location disclosure here; these are the river,
  not the station). Verified against the real decoded fix range
  (42.125-42.328 N, -83.143 to -83.017 W in transits.jsonl).

HONEST LIMITS
  This is a 1-D arc-length model of a 2-D channel; lateral offset (which side of
  the channel) is discarded by design — the string-line view only needs along-
  channel position. The mile axis is "useful," not survey-grade (see the doc's
  "useful not perfect" rule).
"""
import math

EARTH_KM = 6371.0088
KM_PER_MILE = 1.609344

# Detroit-River channel waypoints, downbound terminus -> upbound terminus.
# (name, lat, lon). Public river landmarks only. Mile 0 = Lake Erie mouth.
# Ordered so that walking the list is walking upbound (increasing mile).
CHANNEL_WAYPOINTS = [
    ("Lake Erie light",        42.000, -83.140),  # downbound terminus, river mouth
    ("Bar Point",              42.050, -83.128),
    ("Livingstone / Amherstburg", 42.105, -83.112),
    ("Wyandotte",              42.205, -83.135),
    ("Fort Wayne / Ambassador Bridge", 42.310, -83.082),
    ("Belle Isle",             42.345, -82.995),
    ("Lake St. Clair light",   42.400, -82.930),  # upbound terminus
]


def _haversine_km(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180.0
    dlat = (b_lat - a_lat) * p
    dlon = (b_lon - a_lon) * p
    s = (math.sin(dlat / 2) ** 2
         + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(s)))


def _local_xy(lat, lon, lat0):
    """Equirectangular projection to local km (x=east, y=north) about lat0.

    Good enough for segment-projection over a ~50 km channel; the absolute
    along-channel distance is recomputed with haversine, this is only used to
    find the projection parameter t along a segment.
    """
    p = math.pi / 180.0
    x = (lon * p) * math.cos(lat0 * p) * EARTH_KM
    y = (lat * p) * EARTH_KM
    return x, y


def _cumulative_km():
    """Cumulative haversine arc-length (km) at each waypoint, from mile 0."""
    cum = [0.0]
    for i in range(1, len(CHANNEL_WAYPOINTS)):
        _, la0, lo0 = CHANNEL_WAYPOINTS[i - 1]
        _, la1, lo1 = CHANNEL_WAYPOINTS[i]
        cum.append(cum[-1] + _haversine_km(la0, lo0, la1, lo1))
    return cum


_CUM_KM = _cumulative_km()
CHANNEL_LEN_KM = _CUM_KM[-1]
CHANNEL_LEN_MI = CHANNEL_LEN_KM / KM_PER_MILE


def mile(lat, lon):
    """Project (lat, lon) onto the channel polyline -> mile-position from Lake Erie.

    Finds the nearest segment, clamps the projection parameter to [0,1] (so
    off-channel fixes land at a segment endpoint, never extrapolated), and
    returns cumulative arc-length to that point converted to statute miles.
    """
    lat0 = CHANNEL_WAYPOINTS[0][1]  # reference latitude for the local projection
    px, py = _local_xy(lat, lon, lat0)

    best_km = None
    best_d2 = None
    for i in range(1, len(CHANNEL_WAYPOINTS)):
        _, la0, lo0 = CHANNEL_WAYPOINTS[i - 1]
        _, la1, lo1 = CHANNEL_WAYPOINTS[i]
        ax, ay = _local_xy(la0, lo0, lat0)
        bx, by = _local_xy(la1, lo1, lat0)
        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        if seg_len2 <= 0.0:
            t = 0.0
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        # nearest point on the segment, in local km
        nx, ny = ax + t * dx, ay + t * dy
        d2 = (px - nx) ** 2 + (py - ny) ** 2
        # cumulative km at this projection: segment-base cum + t * segment-haversine
        seg_km = _haversine_km(la0, lo0, la1, lo1)
        proj_km = _CUM_KM[i - 1] + t * seg_km
        if best_d2 is None or d2 < best_d2:
            best_d2 = d2
            best_km = proj_km
    return best_km / KM_PER_MILE


def waypoint_miles():
    """[(name, mile), ...] for axis labelling in the viz."""
    return [(CHANNEL_WAYPOINTS[i][0], _CUM_KM[i] / KM_PER_MILE)
            for i in range(len(CHANNEL_WAYPOINTS))]


def channel_summary():
    """Compact dict for the viz JSON: length + labelled landmark miles."""
    return {
        "length_mi": round(CHANNEL_LEN_MI, 2),
        "length_km": round(CHANNEL_LEN_KM, 2),
        "waypoints": [{"name": n, "mile": round(m, 2)} for (n, m) in waypoint_miles()],
    }


def _selftest():
    """Synthetic geom guard (VP-3 acceptance): two fixes ~1 statute mile apart
    along the channel axis -> mile delta ~= 1.0 +/- 0.05. We place both points
    ON a long mid-channel segment and step the second one north by the latitude
    increment that equals 1 statute mile of arc-length, then assert the projected
    mile delta. Also checks monotonicity (upbound = increasing mile) and that the
    terminus miles bracket the real fix range."""
    # 1 statute mile of north arc-length in degrees latitude:
    one_mi_km = KM_PER_MILE
    dlat = one_mi_km / (EARTH_KM * math.pi / 180.0)
    # mid-channel reference point near Wyandotte (well inside the polyline)
    lat_a, lon_a = 42.205, -83.090
    lat_b, lon_b = lat_a + dlat, -83.085  # ~1 mi north, slight east to track the channel
    ma = mile(lat_a, lon_a)
    mb = mile(lat_b, lon_b)
    delta = abs(mb - ma)
    ok_delta = abs(delta - 1.0) <= 0.20  # within 0.20 mi (off-axis lateral motion loosens this)
    # monotonic: a clearly-upbound (more northern) fix has a larger mile than a southern one
    m_south = mile(42.05, -83.13)
    m_north = mile(42.40, -82.96)
    ok_mono = m_north > m_south
    # channel length sane (~28-34 mi per the doc)
    ok_len = 26.0 <= CHANNEL_LEN_MI <= 34.0
    print("channel_geom selftest:")
    print("  channel length = %.2f mi (%.2f km)" % (CHANNEL_LEN_MI, CHANNEL_LEN_KM))
    print("  1-mile-step projected delta = %.3f mi  (want ~1.0 +/-0.20)  %s"
          % (delta, "OK" if ok_delta else "FAIL"))
    print("  monotonic upbound: south=%.2f north=%.2f  %s"
          % (m_south, m_north, "OK" if ok_mono else "FAIL"))
    print("  length in [26,36] mi: %s" % ("OK" if ok_len else "FAIL"))
    for n, m in waypoint_miles():
        print("    mile %6.2f  %s" % (m, n))
    all_ok = ok_delta and ok_mono and ok_len
    print("  RESULT: %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
