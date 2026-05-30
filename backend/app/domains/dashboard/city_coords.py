"""Known city centroids for KOL map drilldown."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


CityGeo = dict[str, float | str]


CITY_ROWS: list[tuple[str, str, float, float, tuple[str, ...]]] = [
    ("US", "Los Angeles", 34.0522, -118.2437, ("la", "l.a.", "los angeles ca")),
    ("US", "New York", 40.7128, -74.0060, ("nyc", "new york city")),
    ("US", "San Francisco", 37.7749, -122.4194, ("sf", "bay area")),
    ("US", "San Diego", 32.7157, -117.1611, ()),
    ("US", "Sacramento", 38.5816, -121.4944, ()),
    ("US", "Seattle", 47.6062, -122.3321, ()),
    ("US", "Portland", 45.5152, -122.6784, ()),
    ("US", "Las Vegas", 36.1699, -115.1398, ("vegas",)),
    ("US", "Phoenix", 33.4484, -112.0740, ()),
    ("US", "Denver", 39.7392, -104.9903, ()),
    ("US", "Austin", 30.2672, -97.7431, ()),
    ("US", "Dallas", 32.7767, -96.7970, ()),
    ("US", "Houston", 29.7604, -95.3698, ()),
    ("US", "Chicago", 41.8781, -87.6298, ()),
    ("US", "Minneapolis", 44.9778, -93.2650, ()),
    ("US", "Detroit", 42.3314, -83.0458, ()),
    ("US", "Atlanta", 33.7490, -84.3880, ()),
    ("US", "Nashville", 36.1627, -86.7816, ()),
    ("US", "Miami", 25.7617, -80.1918, ()),
    ("US", "Orlando", 28.5383, -81.3792, ()),
    ("US", "Tampa", 27.9506, -82.4572, ()),
    ("US", "Boston", 42.3601, -71.0589, ()),
    ("US", "Philadelphia", 39.9526, -75.1652, ("philly",)),
    ("US", "Washington", 38.9072, -77.0369, ("washington dc", "dc")),
    ("US", "Charlotte", 35.2271, -80.8431, ()),
    ("US", "Raleigh", 35.7796, -78.6382, ()),
    ("US", "Salt Lake City", 40.7608, -111.8910, ("slc",)),
    ("US", "Cleveland", 41.4993, -81.6944, ()),
    ("US", "Pittsburgh", 40.4406, -79.9959, ()),
    ("US", "Cincinnati", 39.1031, -84.5120, ()),
    ("US", "St. Louis", 38.6270, -90.1994, ("saint louis", "st louis")),
    ("US", "Honolulu", 21.3099, -157.8581, ()),
    ("CA", "Toronto", 43.6532, -79.3832, ()),
    ("CA", "Vancouver", 49.2827, -123.1207, ()),
    ("CA", "Montreal", 45.5017, -73.5673, ("montréal",)),
    ("CA", "Calgary", 51.0447, -114.0719, ()),
    ("CA", "Ottawa", 45.4215, -75.6972, ()),
    ("CA", "Edmonton", 53.5461, -113.4938, ()),
    ("GB", "London", 51.5074, -0.1278, ()),
    ("GB", "Manchester", 53.4808, -2.2426, ()),
    ("GB", "Birmingham", 52.4862, -1.8904, ()),
    ("GB", "Bristol", 51.4545, -2.5879, ()),
    ("GB", "Edinburgh", 55.9533, -3.1883, ()),
    ("GB", "Glasgow", 55.8642, -4.2518, ()),
    ("DE", "Berlin", 52.5200, 13.4050, ()),
    ("DE", "Munich", 48.1351, 11.5820, ("münchen", "munchen")),
    ("DE", "Hamburg", 53.5511, 9.9937, ()),
    ("DE", "Frankfurt", 50.1109, 8.6821, ()),
    ("DE", "Cologne", 50.9375, 6.9603, ("köln", "koln")),
    ("DE", "Stuttgart", 48.7758, 9.1829, ()),
    ("DE", "Dusseldorf", 51.2277, 6.7735, ("düsseldorf", "duesseldorf")),
    ("FR", "Paris", 48.8566, 2.3522, ()),
    ("FR", "Lyon", 45.7640, 4.8357, ()),
    ("FR", "Marseille", 43.2965, 5.3698, ()),
    ("FR", "Nice", 43.7102, 7.2620, ()),
    ("FR", "Bordeaux", 44.8378, -0.5792, ()),
    ("IT", "Rome", 41.9028, 12.4964, ("roma",)),
    ("IT", "Milan", 45.4642, 9.1900, ("milano",)),
    ("IT", "Turin", 45.0703, 7.6869, ("torino",)),
    ("IT", "Florence", 43.7696, 11.2558, ("firenze",)),
    ("IT", "Venice", 45.4408, 12.3155, ("venezia",)),
    ("ES", "Madrid", 40.4168, -3.7038, ()),
    ("ES", "Barcelona", 41.3874, 2.1686, ()),
    ("ES", "Valencia", 39.4699, -0.3763, ()),
    ("NL", "Amsterdam", 52.3676, 4.9041, ()),
    ("NL", "Rotterdam", 51.9244, 4.4777, ()),
    ("BE", "Brussels", 50.8503, 4.3517, ("bruxelles",)),
    ("BE", "Antwerp", 51.2194, 4.4025, ()),
    ("AT", "Vienna", 48.2082, 16.3738, ("wien",)),
    ("CH", "Zurich", 47.3769, 8.5417, ("zürich",)),
    ("CH", "Geneva", 46.2044, 6.1432, ("genève",)),
    ("SE", "Stockholm", 59.3293, 18.0686, ()),
    ("SE", "Gothenburg", 57.7089, 11.9746, ("goteborg", "göteborg")),
    ("NO", "Oslo", 59.9139, 10.7522, ()),
    ("DK", "Copenhagen", 55.6761, 12.5683, ("københavn", "kobenhavn")),
    ("FI", "Helsinki", 60.1699, 24.9384, ()),
    ("PL", "Warsaw", 52.2297, 21.0122, ("warszawa",)),
    ("PL", "Krakow", 50.0647, 19.9450, ("kraków",)),
    ("PT", "Lisbon", 38.7223, -9.1393, ("lisboa",)),
    ("PT", "Porto", 41.1579, -8.6291, ()),
    ("IE", "Dublin", 53.3498, -6.2603, ()),
    ("JP", "Tokyo", 35.6762, 139.6503, ("東京",)),
    ("JP", "Osaka", 34.6937, 135.5023, ("大阪",)),
    ("JP", "Kyoto", 35.0116, 135.7681, ("京都",)),
    ("JP", "Nagoya", 35.1815, 136.9066, ("名古屋",)),
    ("JP", "Fukuoka", 33.5904, 130.4017, ("福岡",)),
    ("JP", "Yokohama", 35.4437, 139.6380, ("横浜",)),
    ("JP", "Sapporo", 43.0618, 141.3545, ("札幌",)),
    ("KR", "Seoul", 37.5665, 126.9780, ("서울",)),
    ("KR", "Busan", 35.1796, 129.0756, ("부산",)),
    ("CN", "Beijing", 39.9042, 116.4074, ("北京",)),
    ("CN", "Shanghai", 31.2304, 121.4737, ("上海",)),
    ("CN", "Shenzhen", 22.5431, 114.0579, ("深圳",)),
    ("CN", "Guangzhou", 23.1291, 113.2644, ("广州",)),
    ("CN", "Hangzhou", 30.2741, 120.1551, ("杭州",)),
    ("CN", "Chengdu", 30.5728, 104.0668, ("成都",)),
    ("HK", "Hong Kong", 22.3193, 114.1694, ("香港",)),
    ("TW", "Taipei", 25.0330, 121.5654, ("台北",)),
    ("TW", "Taichung", 24.1477, 120.6736, ("台中",)),
    ("TW", "Kaohsiung", 22.6273, 120.3014, ("高雄",)),
    ("SG", "Singapore", 1.3521, 103.8198, ("新加坡",)),
    ("AU", "Sydney", -33.8688, 151.2093, ()),
    ("AU", "Melbourne", -37.8136, 144.9631, ()),
    ("AU", "Brisbane", -27.4698, 153.0251, ()),
    ("AU", "Perth", -31.9523, 115.8613, ()),
    ("AU", "Adelaide", -34.9285, 138.6007, ()),
    ("NZ", "Auckland", -36.8509, 174.7645, ()),
    ("NZ", "Wellington", -41.2865, 174.7762, ()),
    ("IN", "Mumbai", 19.0760, 72.8777, ()),
    ("IN", "Delhi", 28.7041, 77.1025, ("new delhi",)),
    ("IN", "Bengaluru", 12.9716, 77.5946, ("bangalore",)),
    ("IN", "Chennai", 13.0827, 80.2707, ()),
    ("IN", "Hyderabad", 17.3850, 78.4867, ()),
    ("BR", "Sao Paulo", -23.5558, -46.6396, ("são paulo",)),
    ("BR", "Rio de Janeiro", -22.9068, -43.1729, ("rio",)),
    ("BR", "Curitiba", -25.4284, -49.2733, ()),
    ("MX", "Mexico City", 19.4326, -99.1332, ("cdmx", "ciudad de mexico")),
    ("MX", "Guadalajara", 20.6597, -103.3496, ()),
    ("MX", "Monterrey", 25.6866, -100.3161, ()),
    ("AR", "Buenos Aires", -34.6037, -58.3816, ()),
    ("CL", "Santiago", -33.4489, -70.6693, ()),
    ("CO", "Bogota", 4.7110, -74.0721, ("bogotá",)),
    ("ID", "Jakarta", -6.2088, 106.8456, ()),
    ("ID", "Denpasar", -8.6500, 115.2167, ("bali",)),
    ("MY", "Kuala Lumpur", 3.1390, 101.6869, ("kl",)),
    ("PH", "Manila", 14.5995, 120.9842, ()),
    ("PH", "Cebu", 10.3157, 123.8854, ()),
    ("TH", "Bangkok", 13.7563, 100.5018, ()),
    ("VN", "Ho Chi Minh City", 10.8231, 106.6297, ("saigon",)),
    ("VN", "Hanoi", 21.0278, 105.8342, ("ha noi",)),
    ("AE", "Dubai", 25.2048, 55.2708, ()),
    ("AE", "Abu Dhabi", 24.4539, 54.3773, ()),
    ("ZA", "Cape Town", -33.9249, 18.4241, ()),
    ("ZA", "Johannesburg", -26.2041, 28.0473, ()),
]


def _normalize_city_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9\s.'-]+", " ", ascii_text).replace(".", " ")
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _make_index() -> dict[str, dict[str, CityGeo]]:
    index: dict[str, dict[str, CityGeo]] = {}
    for country, name, lat, lng, aliases in CITY_ROWS:
        row: CityGeo = {"country": country, "name": name, "lat": float(lat), "lng": float(lng)}
        keys = {name, *aliases}
        for key in keys:
            normalized = _normalize_city_text(key)
            if normalized:
                index.setdefault(country, {})[normalized] = row
    return index


CITY_INDEX = _make_index()


def country_city_centroids(country_code: str) -> list[CityGeo]:
    country = str(country_code or "").strip().upper()
    seen: set[str] = set()
    rows: list[CityGeo] = []
    for raw_country, name, lat, lng, _aliases in CITY_ROWS:
        if raw_country != country or name in seen:
            continue
        seen.add(name)
        rows.append({"country": raw_country, "name": name, "lat": float(lat), "lng": float(lng)})
    return rows


def city_geo(country_code: str, city: Any) -> CityGeo | None:
    country = str(country_code or "").strip().upper()
    normalized = _normalize_city_text(city)
    if not normalized:
        return None
    if country and normalized in CITY_INDEX.get(country, {}):
        return dict(CITY_INDEX[country][normalized])
    for cities in CITY_INDEX.values():
        if normalized in cities:
            return dict(cities[normalized])
    return None


def resolve_city(country_code: str, *values: Any) -> CityGeo | None:
    country = str(country_code or "").strip().upper()
    raw_candidates = [str(value or "").strip() for value in values if str(value or "").strip()]
    candidates: list[str] = []
    for value in raw_candidates:
        candidates.append(value)
        candidates.extend(part.strip() for part in re.split(r"[,，/、;；|()\n]+", value) if part.strip())

    for candidate in candidates:
        found = city_geo(country, candidate)
        if found:
            return found

    search_text = _normalize_city_text(" ".join(raw_candidates))
    if not search_text:
        return None
    indexes = [CITY_INDEX.get(country, {})] if country else []
    if not indexes:
        indexes = list(CITY_INDEX.values())
    for city_map in indexes:
        for alias, row in sorted(city_map.items(), key=lambda item: len(item[0]), reverse=True):
            if len(alias) < 4:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", search_text):
                return dict(row)
    return None
