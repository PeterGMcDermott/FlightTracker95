import math
import time
import requests
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# Default Home Base: Franklin, TN
DEFAULT_HOME_LAT = 35.9259
DEFAULT_HOME_LON = -86.8689
DEFAULT_RADIUS_MILES = 30.0

# Airport reference locations
KBNA_LAT, KBNA_LON = 36.1245, -86.6782 # Nashville Intl

AIRLINE_MAP = {
    'SWA': 'Southwest Airlines',
    'DAL': 'Delta Air Lines',
    'AAL': 'American Airlines',
    'UAL': 'United Airlines',
    'ENY': 'Envoy Air (American Eagle)',
    'EDV': 'Endeavor Air (Delta Connection)',
    'RPA': 'Republic Airways',
    'SKW': 'SkyWest Airlines',
    'FDX': 'FedEx Express Cargo',
    'UPS': 'UPS Airlines Cargo',
    'CXK': 'Executive Jet Management',
    'JBU': 'JetBlue Airways',
    'FFT': 'Frontier Airlines',
    'NKS': 'Spirit Airlines',
    'ASA': 'Alaska Airlines',
    'GTI': 'Atlas Air',
    'AWI': 'Air Wisconsin',
    'G7': 'GoJet Airlines',
    'EJA': 'NetJets Aviation'
}

AIRCRAFT_TYPE_MAP = {
    'B737': 'Boeing 737-700/800',
    'B738': 'Boeing 737-800',
    'B739': 'Boeing 737-900',
    'B38M': 'Boeing 737 MAX 8',
    'A320': 'Airbus A320',
    'A321': 'Airbus A321',
    'A319': 'Airbus A319',
    'A20N': 'Airbus A320neo',
    'B752': 'Boeing 757-200',
    'B763': 'Boeing 767-300',
    'B772': 'Boeing 777-200',
    'B789': 'Boeing 787-9 Dreamliner',
    'E75L': 'Embraer E175',
    'E175': 'Embraer E175',
    'CRJ2': 'Bombardier CRJ-200',
    'CRJ7': 'Bombardier CRJ-700',
    'CRJ9': 'Bombardier CRJ-900',
    'C172': 'Cessna 172 Skyhawk',
    'SR22': 'Cirrus SR22',
    'BE20': 'Beechcraft Super King Air',
    'PC12': 'Pilatus PC-12'
}

# Cache for ADSB.lol aircraft metadata (ICAO24 -> {registration, type_code})
adsb_metadata_cache = {}

cache = {
    "timestamp": 0,
    "key": None,
    "flights": [],
    "status": "idle"
}

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8 # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_cardinal_direction(heading):
    if heading is None:
        return "N/A"
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(heading / 22.5) % 16
    return directions[idx]

def resolve_airline(callsign):
    if not callsign or callsign == "UNKNOWN":
        return "Unknown Operator"
    prefix = callsign[:3]
    if prefix in AIRLINE_MAP:
        return AIRLINE_MAP[prefix]
    if callsign.startswith("N") and any(c.isdigit() for c in callsign):
        return "General Aviation / Private"
    return "Commercial / Regional"

def infer_route(callsign, lat, lon, altitude_ft, vert_rate_fpm, heading, on_ground):
    """Smart route resolver for Nashville airspace."""
    dist_from_bna = haversine_miles(KBNA_LAT, KBNA_LON, lat, lon)
    
    if on_ground:
        if dist_from_bna < 8:
            return {"origin": "KBNA (Nashville)", "destination": "Gate / Runway", "route_str": "KBNA Terminal"}
        return {"origin": "Local Airfield", "destination": "On Ground", "route_str": "On Ground"}
    
    # Departing Nashville (climbing within 25 miles of KBNA)
    if dist_from_bna < 25 and vert_rate_fpm > 150:
        dest = "En-Route"
        if 150 <= heading < 220:
            dest = "ATL (Atlanta) / MCO"
        elif 220 <= heading < 270:
            dest = "DFW (Dallas) / IAH"
        elif 270 <= heading < 330:
            dest = "DEN (Denver) / ORD"
        elif 330 <= heading or heading < 45:
            dest = "ORD (Chicago) / DTW"
        elif 45 <= heading < 150:
            dest = "CLT (Charlotte) / DCA"
        return {"origin": "KBNA (Nashville)", "destination": dest, "route_str": f"KBNA ➔ {dest.split()[0]}"}

    # Arriving into Nashville (descending towards KBNA)
    if dist_from_bna < 35 and vert_rate_fpm < -150:
        orig = "En-Route"
        if 330 <= heading or heading < 45:
            orig = "ATL / MCO"
        elif 45 <= heading < 130:
            orig = "DFW / DEN"
        elif 130 <= heading < 210:
            orig = "ORD / DTW"
        elif 210 <= heading < 330:
            orig = "CLT / DCA"
        return {"origin": orig, "destination": "KBNA (Nashville)", "route_str": f"{orig.split()[0]} ➔ KBNA"}

    # High Altitude Transiting Airspace (> 24,000 ft)
    if altitude_ft > 24000:
        if 140 <= heading <= 220:
            return {"origin": "ORD / MDW", "destination": "MCO / TPA", "route_str": "ORD ➔ MCO"}
        elif 320 <= heading or heading <= 40:
            return {"origin": "ATL / Florida", "destination": "ORD / MSP", "route_str": "ATL ➔ ORD"}
        elif 220 < heading < 320:
            return {"origin": "CLT / RDU", "destination": "DFW / DEN", "route_str": "CLT ➔ DFW"}
        elif 40 < heading < 140:
            return {"origin": "DFW / IAH", "destination": "CLT / DCA", "route_str": "DFW ➔ CLT"}

    return {"origin": "Airspace Transit", "destination": "En-Route", "route_str": "En-Route"}

def fetch_adsb_lol_metadata(icao24, callsign):
    """Fetch real-time registration tail number & aircraft type code."""
    if icao24 in adsb_metadata_cache:
        return adsb_metadata_cache[icao24]
    
    try:
        url = f"https://api.adsb.lol/v2/callsign/{callsign.strip()}"
        resp = requests.get(url, timeout=1.5)
        if resp.status_code == 200:
            ac_list = resp.json().get("ac", [])
            if ac_list:
                info = ac_list[0]
                reg = info.get("r", f"N-{icao24.upper()}")
                t_code = info.get("t", "Commercial")
                t_name = AIRCRAFT_TYPE_MAP.get(t_code, f"Aircraft ({t_code})")
                meta = {"registration": reg, "type_code": t_code, "type_name": t_name}
                adsb_metadata_cache[icao24] = meta
                return meta
    except Exception:
        pass

    fallback_reg = callsign if callsign.startswith("N") else f"N-{icao24.upper()}"
    return {"registration": fallback_reg, "type_code": "A320/B737", "type_name": "Commercial Jet"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/geocode")
def geocode():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"status": "error", "message": "Address query parameter is required."}), 400
    
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(address)}&format=json&limit=1"
        headers = {'User-Agent': 'NashvilleFlightTracker/3.0'}
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                res = data[0]
                return jsonify({
                    "status": "success",
                    "lat": float(res["lat"]),
                    "lon": float(res["lon"]),
                    "display_name": res.get("display_name", address)
                })
            else:
                return jsonify({"status": "error", "message": f"Address '{address}' not found."}), 404
        return jsonify({"status": "error", "message": f"Geocoding service returned HTTP {resp.status_code}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/flights")
def flights():
    try:
        home_lat = float(request.args.get("home_lat", DEFAULT_HOME_LAT))
        home_lon = float(request.args.get("home_lon", DEFAULT_HOME_LON))
        radius_miles = float(request.args.get("radius", DEFAULT_RADIUS_MILES))
    except ValueError:
        home_lat = DEFAULT_HOME_LAT
        home_lon = DEFAULT_HOME_LON
        radius_miles = DEFAULT_RADIUS_MILES

    lat_delta = (radius_miles + 5.0) / 69.0
    lon_delta = (radius_miles + 5.0) / (69.0 * math.cos(math.radians(home_lat)))
    
    lamin = round(home_lat - lat_delta, 2)
    lamax = round(home_lat + lat_delta, 2)
    lomin = round(home_lon - lon_delta, 2)
    lomax = round(home_lon + lon_delta, 2)

    cache_key = (lamin, lamax, lomin, lomax)
    current_time = time.time()
    
    if cache["key"] == cache_key and (current_time - cache["timestamp"] < 10) and cache["flights"]:
        for f in cache["flights"]:
            f["dist_miles"] = round(haversine_miles(home_lat, home_lon, f["lat"], f["lon"]), 1)
        cache["flights"].sort(key=lambda x: x["dist_miles"])
        return jsonify({
            "status": "success",
            "cached": True,
            "count": len(cache["flights"]),
            "home": {"lat": home_lat, "lon": home_lon, "radius_miles": radius_miles},
            "flights": cache["flights"],
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cache["timestamp"]))
        })
    
    opensky_url = f"https://opensky-network.org/api/states/all?lamin={lamin}&lamax={lamax}&lomin={lomin}&lomax={lomax}"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        response = requests.get(opensky_url, headers=headers, timeout=8)
        print(f"DEBUG: OpenSky HTTP Status: {response.status_code}, Body: {response.text[:200]}")
        
        if response.status_code == 200:
            data = response.json()
            states = data.get("states") or []
            
            flight_list = []
            for state in states:
                callsign = (state[1] or "N/A").strip()
                if not callsign:
                    callsign = "UNKNOWN"
                
                lon = state[5]
                lat = state[6]
                
                if lat is None or lon is None:
                    continue

                dist = haversine_miles(home_lat, home_lon, lat, lon)
                
                baro_alt_m = state[7]
                altitude_ft = round(baro_alt_m * 3.28084) if baro_alt_m is not None else 0
                
                velocity_ms = state[9]
                velocity_mph = round(velocity_ms * 2.23694) if velocity_ms is not None else 0
                velocity_kts = round(velocity_ms * 1.94384) if velocity_ms is not None else 0
                
                heading = round(state[10], 1) if state[10] is not None else 0
                vert_rate_ms = state[11]
                vert_rate_fpm = round(vert_rate_ms * 196.85) if vert_rate_ms is not None else 0
                
                on_ground = state[8] if state[8] is not None else False
                squawk = state[14] or "----"
                country = state[2] or "Unknown"

                airline = resolve_airline(callsign)
                route_info = infer_route(callsign, lat, lon, altitude_ft, vert_rate_fpm, heading, on_ground)
                ac_meta = fetch_adsb_lol_metadata(state[0], callsign)
                
                flight_list.append({
                    "icao24": state[0],
                    "callsign": callsign,
                    "airline": airline,
                    "origin": route_info["origin"],
                    "destination": route_info["destination"],
                    "route_str": route_info["route_str"],
                    "registration": ac_meta["registration"],
                    "aircraft_type": ac_meta["type_name"],
                    "type_code": ac_meta["type_code"],
                    "country": country,
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "dist_miles": round(dist, 1),
                    "altitude_ft": altitude_ft,
                    "altitude_m": round(baro_alt_m) if baro_alt_m is not None else 0,
                    "velocity_mph": velocity_mph,
                    "velocity_kts": velocity_kts,
                    "velocity_ms": round(velocity_ms, 1) if velocity_ms is not None else 0,
                    "heading": heading,
                    "cardinal": get_cardinal_direction(heading),
                    "vertical_rate_fpm": vert_rate_fpm,
                    "on_ground": on_ground,
                    "squawk": squawk
                })
            
            flight_list.sort(key=lambda x: x["dist_miles"])
            
            cache["timestamp"] = current_time
            cache["key"] = cache_key
            cache["flights"] = flight_list
            cache["status"] = "live"
            
            return jsonify({
                "status": "success",
                "cached": False,
                "count": len(flight_list),
                "home": {"lat": home_lat, "lon": home_lon, "radius_miles": radius_miles},
                "flights": flight_list,
                "last_updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(current_time))
            })
            
        elif response.status_code == 429:
            return jsonify({
                "status": "rate_limited",
                "cached": True,
                "message": "OpenSky API rate limit reached. Serving cached data.",
                "count": len(cache["flights"]),
                "home": {"lat": home_lat, "lon": home_lon, "radius_miles": radius_miles},
                "flights": cache["flights"],
                "last_updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cache["timestamp"]))
            }), 200
        else:
            return jsonify({
                "status": "api_error",
                "message": f"OpenSky API HTTP {response.status_code}",
                "count": len(cache["flights"]),
                "home": {"lat": home_lat, "lon": home_lon, "radius_miles": radius_miles},
                "flights": cache["flights"]
            }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "count": len(cache["flights"]),
            "home": {"lat": home_lat, "lon": home_lon, "radius_miles": radius_miles},
            "flights": cache["flights"]
        }), 200

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
