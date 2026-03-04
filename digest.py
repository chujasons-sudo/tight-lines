from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

WDFW_API_URL = "https://data.wa.gov/resource/6fex-3r7d.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_geocode_cache: dict[str, tuple[float, float] | None] = {}


def fetch_stocking_data() -> list[dict]:
    """
    Fetch WDFW fish stocking records from the past 7 days.

    Returns a list of dicts with keys:
        water_body, county, species, fish_planted, date_stocked, lat, lon

    Lat/lon are not provided by this dataset and will always be None.
    Returns an empty list if the API call fails.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "$where": f"release_start_date >= '{cutoff_str}'",
        "$limit": 1000,
        "$order": "release_start_date DESC",
    }

    try:
        response = requests.get(WDFW_API_URL, params=params, timeout=15)
        response.raise_for_status()
        raw = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch stocking data: %s", e)
        return []

    records = []
    for row in raw:
        records.append({
            "water_body": row.get("release_location", "Unknown"),
            "county": row.get("county", "Unknown"),
            "species": row.get("species", "Unknown"),
            "fish_planted": int(row["number_released"]) if row.get("number_released") else None,
            "date_stocked": row.get("release_start_date", "")[:10],  # YYYY-MM-DD
            "lat": None,  # not available in this dataset
            "lon": None,
        })

    logger.info("Fetched %d stocking records (past 7 days)", len(records))
    return records


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _geocode_lake(water_body: str) -> tuple[float, float] | None:
    """
    Look up coordinates for a Washington lake using Nominatim (OSM).
    Results are cached in memory. Returns (lat, lon) or None if not found.
    Nominatim requires max 1 request/sec; a 1.1s sleep is applied each call.
    """
    if water_body in _geocode_cache:
        return _geocode_cache[water_body]

    time.sleep(1.1)  # respect Nominatim rate limit
    params = {
        "q": f"{water_body}, Washington State",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    headers = {"User-Agent": "tight-lines-fishing-digest/1.0"}

    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        if results:
            coords = (float(results[0]["lat"]), float(results[0]["lon"]))
            _geocode_cache[water_body] = coords
            return coords
    except requests.RequestException as e:
        logger.warning("Geocoding failed for '%s': %s", water_body, e)

    _geocode_cache[water_body] = None
    return None


def filter_and_sort(records: list[dict]) -> list[dict]:
    """
    Filter and sort stocking records by distance from home (Edmonds, WA).

    - Geocodes any record missing coordinates via Nominatim (OSM)
    - Drops records east of lon -120.5 (Eastern WA)
    - Drops records more than MAX_DISTANCE_MILES from home
    - Sorts remaining records by distance, closest first
    """
    from config import HOME_LAT, HOME_LON, MAX_DISTANCE_MILES

    WEST_WA_LON_CUTOFF = -120.5

    enriched = []
    for r in records:
        lat, lon = r.get("lat"), r.get("lon")

        if lat is None or lon is None:
            coords = _geocode_lake(r["water_body"])
            if coords is None:
                logger.debug("Skipping '%s': could not geocode", r["water_body"])
                continue
            lat, lon = coords

        if lon > WEST_WA_LON_CUTOFF:
            continue

        distance = _haversine_miles(HOME_LAT, HOME_LON, lat, lon)
        if distance > MAX_DISTANCE_MILES:
            continue

        enriched.append({**r, "lat": lat, "lon": lon, "distance_miles": round(distance, 1)})

    enriched.sort(key=lambda r: r["distance_miles"])
    logger.info("%d records within %d miles after filtering", len(enriched), MAX_DISTANCE_MILES)
    return enriched


_HIDDEN_GEMS = [
    ("Lake Serene", "Index, WA"),
    ("Barclay Lake", "Baring, WA"),
    ("Lake Isabel", "Darrington, WA"),
    ("Coal Lake", "Enumclaw, WA"),
    ("Flowing Lake", "Snohomish County, WA"),
]


def format_digest(records: list[dict]) -> dict:
    """
    Format filtered, sorted stocking records into an HTML email digest.

    Returns a dict with keys:
        subject: email subject line (str)
        html:    full HTML email body (str)
    """
    today = datetime.now()
    week_label = today.strftime("%B %-d, %Y")

    # Hidden Gem rotates by ISO week number
    iso_week = today.isocalendar()[1]
    gem_name, gem_location = _HIDDEN_GEMS[iso_week % len(_HIDDEN_GEMS)]

    subject = f"\U0001f3a3 Tight Lines \u2014 Week of {week_label}"

    # --- Section 1: Stocked Last Week ---
    if records:
        rows_html = ""
        for r in records:
            fish_count = f"{r['fish_planted']:,}" if r.get("fish_planted") else "—"
            rows_html += (
                f"<tr>"
                f"<td>{r['water_body']}</td>"
                f"<td>{r['county']}</td>"
                f"<td>{r['species']}</td>"
                f"<td style='text-align:right'>{fish_count}</td>"
                f"<td style='text-align:right'>{r['distance_miles']}</td>"
                f"</tr>\n"
            )
        section1 = f"""
        <table>
          <thead>
            <tr>
              <th>Lake Name</th>
              <th>County</th>
              <th>Species</th>
              <th style="text-align:right">Fish Count</th>
              <th style="text-align:right">Miles from Edmonds</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        """
    else:
        section1 = (
            "<p style='color:#666'>No lakes within range were stocked this week. "
            "Check back next Monday.</p>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
  <style>
    body {{
      margin: 0; padding: 0;
      background: #f5f5f5;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 15px; color: #1a1a1a;
    }}
    .wrapper {{
      max-width: 600px; margin: 24px auto; background: #fff;
      border-radius: 6px; overflow: hidden;
      border: 1px solid #e0e0e0;
    }}
    .header {{
      padding: 24px 28px 16px;
      border-bottom: 2px solid #1a1a1a;
    }}
    .header h1 {{
      margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.3px;
    }}
    .header p {{
      margin: 4px 0 0; font-size: 13px; color: #555;
    }}
    .section {{
      padding: 22px 28px;
      border-bottom: 1px solid #e8e8e8;
    }}
    .section:last-child {{ border-bottom: none; }}
    h2 {{
      margin: 0 0 14px; font-size: 13px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.6px; color: #555;
    }}
    table {{
      width: 100%; border-collapse: collapse; font-size: 14px;
    }}
    th {{
      text-align: left; padding: 6px 8px 8px;
      border-bottom: 2px solid #1a1a1a;
      font-size: 12px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.4px; color: #333;
    }}
    td {{
      padding: 9px 8px; border-bottom: 1px solid #efefef; vertical-align: top;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #fafafa; }}
    .gem-name {{ font-weight: 700; font-size: 16px; margin: 0 0 4px; }}
    .gem-location {{ color: #555; font-size: 14px; margin: 0; }}
    .footer {{
      padding: 16px 28px; background: #f9f9f9;
      font-size: 12px; color: #888; text-align: center;
    }}
    @media (max-width: 480px) {{
      .wrapper {{ margin: 0; border-radius: 0; border-left: none; border-right: none; }}
      .section, .header {{ padding: 18px 16px; }}
      table {{ font-size: 13px; }}
      th, td {{ padding: 7px 5px; }}
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>&#x1F3A3; Tight Lines</h1>
      <p>Week of {week_label} &nbsp;·&nbsp; Western Washington Fishing Digest</p>
    </div>

    <div class="section">
      <h2>Stocked Last Week</h2>
      {section1}
    </div>

    <div class="section">
      <h2>Coming Up</h2>
      <p style="color:#666;margin:0">Advance stocking data coming in v2.</p>
    </div>

    <div class="section">
      <h2>Hidden Gem</h2>
      <p class="gem-name">{gem_name}</p>
      <p class="gem-location">{gem_location}</p>
    </div>

    <div class="footer">
      Stocking data from WDFW &nbsp;·&nbsp;
      Unsubscribe? Reply with "stop"
    </div>
  </div>
</body>
</html>"""

    return {"subject": subject, "html": html}


def send_digest(subject: str, html_content: str) -> bool:
    """
    Send the digest email via Resend.

    Returns True on success, False on failure (never raises).
    """
    import resend
    from config import FROM_EMAIL, RECIPIENT_EMAIL, RESEND_API_KEY

    resend.api_key = RESEND_API_KEY

    try:
        response = resend.Emails.send({
            "from": FROM_EMAIL,
            "to": RECIPIENT_EMAIL,
            "subject": subject,
            "html": html_content,
        })
        logger.info("Email sent successfully (id=%s)", response.get("id"))
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def main() -> None:
    records = fetch_stocking_data()
    logger.info("Fetched %d total records", len(records))

    filtered = filter_and_sort(records)

    digest = format_digest(filtered)
    logger.info("Subject: %s", digest["subject"])

    send_digest(digest["subject"], digest["html"])


if __name__ == "__main__":
    main()
