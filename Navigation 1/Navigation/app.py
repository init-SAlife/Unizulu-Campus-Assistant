import networkx as nx
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pathlib import Path

from campus_graph import Campus, load_buildings


# --------------------------------------------------
# FILE LOCATIONS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

# A user further than this from every campus path is treated as "not on campus"
OFF_CAMPUS_M = 300


# --------------------------------------------------
# FLASK
# --------------------------------------------------

app = Flask(__name__,)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
CORS(app)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR / "static", "index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        return jsonify({"error": "Please enter a question."}), 400

    question_lower = question.lower()
    if "semester 2" in question_lower and "registration" in question_lower:
        answer = (
            "Please confirm the current Semester 2 registration dates with the "
            "UNIZULU registration office or the latest student portal notice."
        )
    elif "route" in question_lower or "direction" in question_lower:
        answer = "Open Campus Navigator, choose a destination, and select Get Route."
    else:
        answer = (
            "I received your question. This local backend is connected, but the "
            "AI knowledge service has not been configured yet."
        )

    return jsonify({"answer": answer})


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    document_type = request.form.get("document_type", "document")

    if not files or all(not file.filename for file in files):
        return jsonify({"error": "Please attach at least one document."}), 400

    accepted_files = []
    failed_files = []
    for file in files:
        filename = secure_filename(file.filename or "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or extension not in ALLOWED_UPLOAD_EXTENSIONS:
            failed_files.append(file.filename or "unnamed file")
            continue
        accepted_files.append(filename)

    if not accepted_files:
        return jsonify({
            "error": "Supported files are PDF, PNG, JPG, and JPEG.",
            "data": {"failed_files": failed_files}
        }), 400

    return jsonify({
        "message": (
            f"Received {len(accepted_files)} {document_type.replace('_', ' ')} "
            "file(s). Document analysis is not configured yet."
        ),
        "data": {
            "accepted_files": accepted_files,
            "failed_files": failed_files
        }
    })


# --------------------------------------------------
# CAMPUS NAVIGATOR: graph from the OSM export + stable building list
# --------------------------------------------------

CAMPUS = Campus()            # reads map_7__1_.osm (see campus_graph.py)
BUILDINGS = load_buildings() # reads buildings.json

# Where to start a route when the user is off campus or their location is unavailable
HOME = next((b for b in BUILDINGS.values() if b["name"].lower() == "main gate"), None) \
    or next((b for b in BUILDINGS.values() if b["category"] == "gate"), None) \
    or next(iter(BUILDINGS.values()))

print(
    f"Campus graph: {CAMPUS.G.number_of_nodes()} nodes, "
    f"{CAMPUS.G.number_of_edges()} edges, {len(BUILDINGS)} buildings"
)
if CAMPUS.islands:
    print(f"NOTE: {len(CAMPUS.islands)} disconnected path piece(s) ignored (not joined to the main network)")
for problem in CAMPUS.check_buildings(BUILDINGS, HOME["id"]):
    print("WARNING:", problem)


@app.route("/api/campus")
def api_campus():
    """Everything the map needs to start: campus bounds + the building list."""
    return jsonify({
        "bounds": CAMPUS.bounds,
        "buildings": list(BUILDINGS.values()),
    })


@app.route("/api/route", methods=["POST"])
def api_route():
    """
    Body: {
        "start": {"lat": .., "lng": ..},     optional -- the user's current GPS position
        "destination_id": "14210600954"      a building id from buildings.json
        (or "end": {"lat": .., "lng": ..}    for an arbitrary destination)
    }
    If start is missing, or the user is far from campus, the route starts at the main gate.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON data received."}), 400

    # ---- destination ----
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

    # ---- start ----
    start_name = "your location"
    off_campus = False
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

    # ---- route ----
    try:
        result = CAMPUS.route(start, end)
    except nx.NetworkXNoPath:
        return jsonify({"error": "No walking path exists between these locations."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    result.update({
        "destination": dest_name,
        "start_name": start_name,
        "off_campus": off_campus,
    })
    return jsonify(result)


# --------------------------------------------------
# START SERVER
# --------------------------------------------------

if __name__ == "__main__":

    print("UNIZULU Campus Navigator API")
    print("Server running at http://127.0.0.1:5000")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
