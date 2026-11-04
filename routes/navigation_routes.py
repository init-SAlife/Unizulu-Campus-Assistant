# Campus Navigator web routes: walking routes on the KwaDlangezwa campus.

import atexit

from flask import Blueprint, jsonify, request

from navigation import parking_status
from navigation.campus_graph import Campus, load_buildings
from navigation.route_service import RouteService, ServerBusy, uptime_s

navigation_bp = Blueprint("navigation", __name__)

# a student further than this from every campus path is treated as "not on campus"
OFF_CAMPUS_M = 300
MAX_AVOID_POINTS = 60   # closed-stretch points accepted per request

# start-up
CAMPUS = Campus()             # reads the .osm file next to campus_graph.py
BUILDINGS = load_buildings()  # reads buildings.json

# the main gate: shown in /api/campus (routes NEVER start here automatically any more --
# without a real starting point the student is asked to use "Set my location")
HOME = next((b for b in BUILDINGS.values() if b["name"].lower() == "main gate"), None) \
    or next((b for b in BUILDINGS.values() if b["category"] == "gate"), None) \
    or next(iter(BUILDINGS.values()))

print(f"[navigator] campus graph: {CAMPUS.G.number_of_nodes()} nodes, "
      f"{CAMPUS.G.number_of_edges()} edges, {len(BUILDINGS)} buildings")
print(f"[navigator] car roads: {CAMPUS.D.number_of_edges()} one-way road pieces, "
      f"{len(CAMPUS.parkings)} parking area(s): " + ", ".join(p["name"] for p in CAMPUS.parkings))
if CAMPUS.islands:
    print(f"[navigator] NOTE: {len(CAMPUS.islands)} disconnected path piece(s) ignored "
          "(not joined to the main network)")
for problem in CAMPUS.check_buildings(BUILDINGS, HOME["id"]):
    print("[navigator] WARNING:", problem)

# parallel route workers + admission limit + cache (see route_service.py)
ROUTES = RouteService(CAMPUS)
atexit.register(ROUTES.shutdown)


def need_location(message, code):
    # The start point is missing or unusable: the page asks the student to set it by hand.
    return jsonify({"error": message, "code": code}), 422


@navigation_bp.route("/api/health", methods=["GET"])
def api_health():
    # For a load balancer / uptime check: is this instance alive, and how busy is it?
    return jsonify({"status": "ok", "uptime_s": uptime_s(), "routing": ROUTES.snapshot()})


# routes
@navigation_bp.route("/api/campus", methods=["GET"])
def api_campus():
    # Everything the map needs to start: campus bounds + the building list.
    return jsonify({
        "bounds": CAMPUS.bounds,
        "buildings": list(BUILDINGS.values()),
        "home": {"id": HOME["id"], "name": HOME["name"]},
        "parkings": parking_status.snapshot(CAMPUS.parkings),
        "parking_full_minutes": parking_status.FULL_MINUTES,
    })


# parking status
PARKINGS_BY_ID = {p["id"]: p for p in CAMPUS.parkings}


@navigation_bp.route("/api/parking", methods=["GET"])
def api_parking():
    # Every parking area with its status (full / has space), for the P markers on the map.
    return jsonify({"parkings": parking_status.snapshot(CAMPUS.parkings),
                    "full_minutes": parking_status.FULL_MINUTES})


@navigation_bp.route("/api/parking/<parking_id>/status", methods=["POST"])
def api_parking_report(parking_id):
    # A driver reports a parking area.
    if parking_id not in PARKINGS_BY_ID:
        return jsonify({"error": "Unknown parking area."}), 404
    data = request.get_json(silent=True) or {}
    status = str(data.get("status", "")).lower()
    if status not in ("full", "available"):
        return jsonify({"error": 'status must be "full" or "available".'}), 400
    client = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    try:
        parking_status.report(parking_id, status, client)
    except parking_status.TooFast:
        return jsonify({"error": "You just reported this parking. Please wait a moment."}), 429
    except Exception as exc:
        print(f"[parking] report could not be saved: {exc}")
        return jsonify({"error": "The report could not be saved."}), 500
    name = PARKINGS_BY_ID[parking_id]["name"]
    print(f"[parking] {name} ({parking_id}) reported {status}")
    return jsonify({"ok": True, "id": parking_id, "name": name, "status": status,
                    "parkings": parking_status.snapshot(CAMPUS.parkings)})


@navigation_bp.route("/api/route", methods=["POST"])
def api_route():
    # works out routes to a building: walk, car + walk or car only, with optional start, avoid and skip_parking
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON data received."}), 400

    # destination
    try:
        if data.get("destination_id") is not None:
            dest = BUILDINGS.get(str(data["destination_id"]))
            if dest is None:
                return jsonify({"error": "Unknown destination."}), 404
            end = (dest["lat"], dest["lng"])
            dest_name = dest["name"]
        else:
            end = (float(data["end"]["lat"]), float(data["end"]["lng"]))
            dest_name = None
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid destination."}), 400

    # start (no fallback: a real point or nothing)
    start_name = "your location"
    if data.get("start_id") is not None:
        origin = BUILDINGS.get(str(data["start_id"]))
        if origin is None:
            return jsonify({"error": "Unknown starting point."}), 404
        start = (origin["lat"], origin["lng"])
        start_name = origin["name"]
    else:
        start = data.get("start")
        if not start:
            return need_location(
                'We could not get your exact location. Tap "Set my location" and tap where you are on the map.',
                "need_location")
        try:
            start = (float(start["lat"]), float(start["lng"]))
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid start coordinates."}), 400
        away = CAMPUS.snap(*start).offset_m
        if away > OFF_CAMPUS_M:
            return need_location(
                f"Your location is {away:.0f} m from the nearest campus path, so it is probably wrong "
                'or you are off campus. Tap "Set my location" and tap where you are on the map.',
                "off_campus")

    mode = str(data.get("mode", "walk")).lower()
    if mode not in ("walk", "car", "drive"):
        mode = "walk"

    # parkings that are full (reported by drivers)
    full, skip = set(), set()
    if mode != "walk":
        full = set(parking_status.full_ids())
        asked = data.get("skip_parking") or []
        if isinstance(asked, list):
            skip = {str(x) for x in asked[:20] if str(x) in PARKINGS_BY_ID}
        full |= skip

    # closed / abandoned stretches to route around
    avoid = []
    try:
        for p in (data.get("avoid") or [])[:MAX_AVOID_POINTS]:
            avoid.append((float(p[0]), float(p[1])))
    except (TypeError, ValueError, IndexError, KeyError):
        return jsonify({"error": "Invalid avoid points."}), 400

    # routes (calculated in parallel worker processes)
    try:
        answer = ROUTES.routes(mode, start, end, avoid, full, skip)
    except ServerBusy:
        resp = jsonify({"error": "Many students are asking for directions right now. Trying again...",
                        "code": "busy"})
        resp.headers["Retry-After"] = "2"
        return resp, 503
    except Exception as exc:
        print(f"[navigator] error routing: {exc}")
        return jsonify({"error": "The route could not be calculated."}), 500
    if not answer.get("ok"):
        if mode == "car":
            return jsonify({"error": "No parking area can be reached by car for this trip. Try walking."}), 404
        if mode == "drive":
            return jsonify({"error": "This destination cannot be reached by road from here. Try car + walk or walking."}), 404
        return jsonify({"error": "No walking path exists between these locations."}), 404
    routes, walk_only_min = answer["routes"], answer["walk_only_min"]

    result = dict(routes[0])      # older pages read the first route from the top level
    result.update({
        "routes": routes,
        "mode": mode,
        "walk_only_min": walk_only_min,
        "destination": dest_name,
        "start_name": start_name,
        "off_campus": False,
        "full_parkings": sorted(full),
    })
    _record_route(mode, dest_name)
    return jsonify(result)


def _record_route(mode, destination):
    # Inside the Campus AI Assistant: count the route for the admin dashboard (usage_events).
    try:
        from services import accounts
        if getattr(accounts, "_pool_settings", None) is None:
            return
        from flask import session
        accounts.log_event("route", session.get("user_id"), f"{mode} to {destination or 'a point on the map'}")
    except Exception as exc:
        print(f"[navigator] route not counted: {exc}")
