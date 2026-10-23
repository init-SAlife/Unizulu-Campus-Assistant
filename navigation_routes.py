import networkx as nx
from flask import Blueprint, jsonify, request

from campus_graph import Campus, load_buildings

navigation_bp = Blueprint("navigation", __name__)

# a student further than this from every campus path is treated as "not on campus"
OFF_CAMPUS_M = 300

# start-up
CAMPUS = Campus()             # reads the .osm file next to campus_graph.py
BUILDINGS = load_buildings()  # reads buildings.json

# where a route starts when the student is off campus or their location is unavailable
HOME = next((b for b in BUILDINGS.values() if b["name"].lower() == "main gate"), None) \
    or next((b for b in BUILDINGS.values() if b["category"] == "gate"), None) \
    or next(iter(BUILDINGS.values()))

print(f"[navigator] campus graph: {CAMPUS.G.number_of_nodes()} nodes, "
      f"{CAMPUS.G.number_of_edges()} edges, {len(BUILDINGS)} buildings")
if CAMPUS.islands:
    print(f"[navigator] NOTE: {len(CAMPUS.islands)} disconnected path piece(s) ignored "
          "(not joined to the main network)")
for problem in CAMPUS.check_buildings(BUILDINGS, HOME["id"]):
    print("[navigator] WARNING:", problem)


# routes
@navigation_bp.route("/api/campus", methods=["GET"])
def api_campus():
    """Everything the map needs to start: campus bounds + the building list."""
    return jsonify({
        "bounds": CAMPUS.bounds,
        "buildings": list(BUILDINGS.values()),
        "home": {"id": HOME["id"], "name": HOME["name"]},
    })


@navigation_bp.route("/api/route", methods=["POST"])
def api_route():
    """
    Body: {
        "destination_id": "14210600954"      a building id from buildings.json
            (or "end": {"lat": .., "lng": ..}  for an arbitrary destination)
        "start_id": "14055010324"            optional -- start at this building ("From" list)
        "start": {"lat": .., "lng": ..}      optional -- the student's current GPS position
    }
    With neither start_id nor start, or when the student is far from campus,
    the route starts at the main gate.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON data received."}), 400

    #  destination
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

    #  start 
    start_name = "your location"
    off_campus = False
    if data.get("start_id") is not None:
        origin = BUILDINGS.get(str(data["start_id"]))
        if origin is None:
            return jsonify({"error": "Unknown starting point."}), 404
        start = (origin["lat"], origin["lng"])
        start_name = origin["name"]
    else:
        start = data.get("start")
        try:
            start = (float(start["lat"]), float(start["lng"])) if start else None
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid start coordinates."}), 400

        if start is not None and CAMPUS.snap(*start).offset_m > OFF_CAMPUS_M:
            off_campus = True
            start = None
        if start is None:
            start = (HOME["lat"], HOME["lng"])
            start_name = HOME["name"]

    #  route 
    try:
        result = CAMPUS.route(start, end)
    except nx.NetworkXNoPath:
        return jsonify({"error": "No walking path exists between these locations."}), 404
    except Exception as exc:
        print(f"[navigator] error routing: {exc}")
        return jsonify({"error": "The route could not be calculated."}), 500

    result.update({
        "destination": dest_name,
        "start_name": start_name,
        "off_campus": off_campus,
    })
    return jsonify(result)
