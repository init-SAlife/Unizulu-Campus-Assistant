"""
campus_graph.py -- walking graph + stable building list for the UNIZULU navigator.

Everything is built from your OpenStreetMap export (the .osm file), because that is
the file that always has your latest footpaths and your named building points.

    python campus_graph.py            write buildings.json from the .osm file (first time only)
    python campus_graph.py --force    overwrite buildings.json (this discards manual edits!)

app.py imports Campus and load_buildings from here.
"""

import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

BASE_DIR = Path(__file__).resolve().parent
BUILDINGS_FILE = BASE_DIR / "buildings.json"

def find_osm_file():
    """Use the .osm file sitting next to this script (the newest one if there are several)."""
    files = sorted(BASE_DIR.glob("*.osm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(
            f"No .osm file found in {BASE_DIR}. Copy your OpenStreetMap export into that folder."
        )
    return files[0]


# highway= values people cannot (or should not) be routed along
NOT_WALKABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "proposed", "construction", "raceway", "bus_guideway",
}
GATE_BARRIERS = {"entrance", "gate", "lift_gate"}


# --------------------------------------------------
# OSM PARSING
# --------------------------------------------------

def haversine(lat1, lng1, lat2, lng2):
    """Distance between two lat/lng points in metres."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_osm(path):
    root = ET.parse(path).getroot()

    nodes, node_tags = {}, {}
    for n in root.iter("node"):
        nid = n.get("id")
        nodes[nid] = (float(n.get("lat")), float(n.get("lon")))
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if tags:
            node_tags[nid] = tags

    ways = []
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.findall("nd")]
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        ways.append((refs, tags))

    b = root.find("bounds")
    bounds = None
    if b is not None:
        bounds = {
            "south": float(b.get("minlat")), "west": float(b.get("minlon")),
            "north": float(b.get("maxlat")), "east": float(b.get("maxlon")),
        }
    return {"nodes": nodes, "node_tags": node_tags, "ways": ways, "bounds": bounds}


# --------------------------------------------------
# STABLE BUILDING LIST
# --------------------------------------------------

def extract_buildings(osm):
    """
    Your hand-placed, named OSM points become the building list:
      * points with an alt_name (Library, D block, Admin, Clinic ...)
      * gates / entrances
      * community centre and bus station
    Waypoint labels such as A1, B12, F5 are ignored.
    """
    out = []
    for nid, tags in osm["node_tags"].items():
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        is_gate = tags.get("barrier") in GATE_BARRIERS
        amenity = tags.get("amenity")
        if not (is_gate or "alt_name" in tags or amenity in {"community_centre", "bus_station"}):
            continue
        lat, lng = osm["nodes"][nid]
        out.append({
            "id": nid,  # OSM node id: never changes, even if you rename the building
            "name": name,
            "description": (tags.get("alt_name") or "").strip(),
            "category": "gate" if is_gate else ("transport" if amenity == "bus_station" else "building"),
            "lat": round(lat, 7),
            "lng": round(lng, 7),
        })
    out.sort(key=lambda b: b["name"].lower())
    return out


def load_buildings(path=BUILDINGS_FILE):
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not found. Run:  python campus_graph.py")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(b["id"]): b for b in data["buildings"]}


# --------------------------------------------------
# GRAPH + SNAPPING + ROUTING
# --------------------------------------------------

@dataclass
class Snap:
    u: str          # segment start node
    v: str          # segment end node
    t: float        # 0..1 position along u->v
    lat: float      # snapped point
    lng: float
    offset_m: float # distance from the original point to the path


class Campus:
    def __init__(self, osm_path=None):
        osm = parse_osm(osm_path or find_osm_file())
        self.osm = osm
        full = self._build_graph(osm)
        if full.number_of_nodes() == 0:
            raise RuntimeError("No walkable paths found in the OSM file.")

        # Route only on the main connected network. Small islands (a footpath that is not
        # joined to anything) would otherwise attract snapping and then fail with "no path".
        pieces = sorted(nx.connected_components(full), key=len, reverse=True)
        self.G = full.subgraph(pieces[0]).copy()
        self.islands = [
            {"nodes": len(p), "lat": full.nodes[next(iter(p))]["lat"], "lng": full.nodes[next(iter(p))]["lng"]}
            for p in pieces[1:]
        ]

        lats = [d["lat"] for _, d in self.G.nodes(data=True)]
        lngs = [d["lng"] for _, d in self.G.nodes(data=True)]
        self.bounds = osm["bounds"] or {
            "south": min(lats), "north": max(lats), "west": min(lngs), "east": max(lngs)
        }

        # local flat-earth projection (metres) -- accurate to well under 1 % on a campus
        self._lat0 = (self.bounds["south"] + self.bounds["north"]) / 2
        self._lng0 = (self.bounds["west"] + self.bounds["east"]) / 2
        self._kx = 111320 * math.cos(math.radians(self._lat0))
        self._ky = 110540

        self._segments = []
        for u, v in self.G.edges:
            x1, y1 = self._xy(self.G.nodes[u]["lat"], self.G.nodes[u]["lng"])
            x2, y2 = self._xy(self.G.nodes[v]["lat"], self.G.nodes[v]["lng"])
            self._segments.append((u, v, x1, y1, x2, y2))

    # ---- build ----
    @staticmethod
    def _build_graph(osm):
        """
        Every vertex of every walkable way becomes a graph node, and every piece of a way
        between two vertices becomes an edge. Two OSM nodes sitting on exactly the same
        coordinates are welded into one, so a footpath that ends "on top of" another one
        still connects even if the two points were never merged in the iD editor.
        """
        nodes = osm["nodes"]

        def key(ref):
            lat, lng = nodes[ref]
            return f"{lat:.7f},{lng:.7f}"

        G = nx.Graph()
        for refs, tags in osm["ways"]:
            hw = tags.get("highway")
            if not hw or hw in NOT_WALKABLE:
                continue
            if tags.get("foot") == "no":
                continue
            if tags.get("access") in {"no", "private"} and tags.get("foot") not in {"yes", "permissive", "designated"}:
                continue
            refs = [r for r in refs if r in nodes]
            for a, b in zip(refs, refs[1:]):
                ka, kb = key(a), key(b)
                if ka == kb:
                    continue
                for ref, k in ((a, ka), (b, kb)):
                    if k not in G:
                        G.add_node(k, lat=nodes[ref][0], lng=nodes[ref][1])
                G.add_edge(ka, kb, distance=haversine(*nodes[a], *nodes[b]))
        return G

    # ---- projection helpers ----
    def _xy(self, lat, lng):
        return (lng - self._lng0) * self._kx, (lat - self._lat0) * self._ky

    def _latlng(self, x, y):
        return self._lat0 + y / self._ky, self._lng0 + x / self._kx

    # ---- snapping ----
    def snap(self, lat, lng):
        """Closest point on ANY path segment (not just the closest junction)."""
        px, py = self._xy(lat, lng)
        best = None
        for u, v, x1, y1, x2, y2 in self._segments:
            dx, dy = x2 - x1, y2 - y1
            length2 = dx * dx + dy * dy
            t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
            qx, qy = x1 + t * dx, y1 + t * dy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            if best is None or d2 < best[0]:
                best = (d2, u, v, t, qx, qy)
        d2, u, v, t, qx, qy = best
        qlat, qlng = self._latlng(qx, qy)
        return Snap(u, v, t, qlat, qlng, math.sqrt(d2))

    # ---- routing ----
    def _attach(self, H, name, s):
        seg_len = self.G[s.u][s.v]["distance"]
        H.add_node(name, lat=s.lat, lng=s.lng)
        H.add_edge(name, s.u, distance=s.t * seg_len)
        H.add_edge(name, s.v, distance=(1 - s.t) * seg_len)

    def route(self, start, end):
        """
        start / end = (lat, lng). Returns the walking route from the closest point on
        the path network to `start`, to the closest point to `end`.
        Raises nx.NetworkXNoPath if the two are on disconnected pieces of the map.
        """
        s, e = self.snap(*start), self.snap(*end)

        H = self.G.copy()  # tiny graph, so a copy per request is cheap
        self._attach(H, "__start__", s)
        self._attach(H, "__end__", e)
        if (s.u, s.v) == (e.u, e.v):  # both on the same segment: walk straight along it
            seg_len = self.G[s.u][s.v]["distance"]
            H.add_edge("__start__", "__end__", distance=abs(s.t - e.t) * seg_len)

        nodes = nx.shortest_path(H, "__start__", "__end__", weight="distance")
        path_m = sum(H[a][b]["distance"] for a, b in zip(nodes, nodes[1:]))
        coords = [[H.nodes[n]["lat"], H.nodes[n]["lng"]] for n in nodes]

        total = path_m + s.offset_m + e.offset_m
        return {
            "coordinates": coords,
            "start_connector": [[start[0], start[1]], [s.lat, s.lng]],
            "end_connector": [[e.lat, e.lng], [end[0], end[1]]],
            "distance_meters": round(total, 1),
            "estimated_walk_time_min": round(total / 80, 1),
            "start_offset_m": round(s.offset_m, 1),
            "end_offset_m": round(e.offset_m, 1),
        }

    # ---- sanity checks (printed when the server starts) ----
    def check_buildings(self, buildings, home_id=None):
        problems = []
        home = buildings.get(home_id) if home_id else None
        for b in buildings.values():
            sn = self.snap(b["lat"], b["lng"])
            if sn.offset_m > 50:
                problems.append(f"{b['name']} ({b['id']}) is {sn.offset_m:.0f} m from the nearest path")
            if home:
                try:
                    self.route((home["lat"], home["lng"]), (b["lat"], b["lng"]))
                except nx.NetworkXNoPath:
                    problems.append(f"{b['name']} ({b['id']}) cannot be reached from {home['name']} -- a gap in the footpaths")
        return problems


# --------------------------------------------------
# CLI: write buildings.json
# --------------------------------------------------

if __name__ == "__main__":
    if BUILDINGS_FILE.exists() and "--force" not in sys.argv:
        print(f"{BUILDINGS_FILE.name} already exists, so your edits are safe. Use --force to overwrite it.")
        sys.exit(0)
    osm = parse_osm(find_osm_file())
    buildings = extract_buildings(osm)
    BUILDINGS_FILE.write_text(
        json.dumps({"buildings": buildings}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(buildings)} buildings to {BUILDINGS_FILE.name}")
