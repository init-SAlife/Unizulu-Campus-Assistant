# walking graph + stable building list for the UNIZULU navigator.

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
    # Use the .osm file sitting next to this script (the newest one if there are several).
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

# highway= values a car may drive on (everything else -- footway, track, path, steps -- is walk-only)
CAR_HIGHWAYS = {
    "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service", "road",
}
# service= values that are not normal driving roads
CAR_BLOCKED_SERVICE = {"emergency_access"}

WALK_M_PER_MIN = 80      # ~4.8 km/h
DRIVE_M_PER_MIN = 333    # ~20 km/h inside the campus
PARKING_MIN = 1.0        # time to find a bay and park

# alternative routes ("Alternate" button)
ALT_PENALTY = 2.5        # roads already used cost this much more when looking for the next route
ALT_MAX_RATIO = 1.6      # an alternative may be at most 60 % longer than the shortest...
ALT_SLACK_M = 120        # ...plus this many metres (short campus walks need some slack)
ALT_MAX_OVERLAP = 0.7    # ...and must share at most 70 % of its length with a route already offered

# closed / abandoned stretches ("avoid" points sent by the page when the student turns back,
# leaves the route, or taps "Road closed")
AVOID_FACTOR = 40
AVOID_SNAP_M = 15        # an avoid point further than this from every path is ignored


def is_drivable(tags):
    if tags.get("highway") not in CAR_HIGHWAYS:
        return False
    if tags.get("service") in CAR_BLOCKED_SERVICE:
        return False
    if tags.get("access") in {"no", "private"} or tags.get("motor_vehicle") in {"no", "private"} \
            or tags.get("motorcar") in {"no", "private"}:
        return False
    return True


# OSM PARSING

def haversine(lat1, lng1, lat2, lng2):
    # Distance between two lat/lng points in metres.
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

    ways, way_ids = [], []
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.findall("nd")]
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        ways.append((refs, tags))
        way_ids.append(w.get("id"))

    b = root.find("bounds")
    bounds = None
    if b is not None:
        bounds = {
            "south": float(b.get("minlat")), "west": float(b.get("minlon")),
            "north": float(b.get("maxlat")), "east": float(b.get("maxlon")),
        }
    return {"nodes": nodes, "node_tags": node_tags, "ways": ways, "way_ids": way_ids, "bounds": bounds}


# STABLE BUILDING LIST

def extract_buildings(osm):
    # Your hand-placed, named OSM points become the building list
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
        raise FileNotFoundError(f"{path} not found. Run:  python -m navigation.campus_graph")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(b["id"]): b for b in data["buildings"]}


# GRAPH + SNAPPING + ROUTING

JOIN_GAP_M = 6.0   # a footpath that stops closer than this to another path is joined to it


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

        # car network: the drivable roads only, one-way roads respected
        D = nx.DiGraph()
        for u, v, d in self.G.edges(data=True):
            if not d.get("car"):
                continue
            for a in (u, v):
                D.add_node(a, **self.G.nodes[a])
            ow = d.get("oneway_from")
            if ow is None or ow == u:
                D.add_edge(u, v, distance=d["distance"])
            if ow is None or ow == v:
                D.add_edge(v, u, distance=d["distance"])
        if D.number_of_nodes():
            main = max(nx.weakly_connected_components(D), key=len)
            D = D.subgraph(main).copy()
        self.D = D
        self._car_segments = [s for s in self._segments if D.has_node(s[0]) and D.has_node(s[1])
                              and (D.has_edge(s[0], s[1]) or D.has_edge(s[1], s[0]))]
        self.parkings = self._find_parkings(osm)

    # build
    @staticmethod
    def _build_graph(osm):
        # Every vertex of every walkable way becomes a graph node, and every piece of a way
        # between two vertices becomes an edge.
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
            car = is_drivable(tags)
            oneway = tags.get("oneway") in {"yes", "1", "true", "-1"}
            if tags.get("oneway") == "-1":
                refs = refs[::-1]          # store one-way roads in their driving direction
            for a, b in zip(refs, refs[1:]):
                ka, kb = key(a), key(b)
                if ka == kb:
                    continue
                for ref, k in ((a, ka), (b, kb)):
                    if k not in G:
                        G.add_node(k, lat=nodes[ref][0], lng=nodes[ref][1])
                try:
                    layer = int(tags.get("layer", 0))
                except ValueError:
                    layer = 0
                if G.has_edge(ka, kb) and G[ka][kb].get("car") and not car:
                    continue               # a footway drawn on top of a road: keep the road
                G.add_edge(ka, kb, distance=haversine(*nodes[a], *nodes[b]), layer=layer,
                           car=car, oneway_from=(ka if (car and oneway) else None))
        return Campus._repair(G)

    # repair the map so routes are really the shortest
    @staticmethod
    def _repair(G):
        # OpenStreetMap drawings often have two small mistakes that make routes longer than they
        # should be, or fail
        if G.number_of_edges() == 0:
            return G
        lat0 = sum(d["lat"] for _, d in G.nodes(data=True)) / G.number_of_nodes()
        kx, ky = 111320 * math.cos(math.radians(lat0)), 110540

        def xy(n):
            return G.nodes[n]["lng"] * kx, G.nodes[n]["lat"] * ky

        def pkey(x, y):
            return f"{y / ky:.7f},{x / kx:.7f}"

        edges = [(u, v, G[u][v].get("layer", 0)) for u, v in G.edges]
        geo = {(u, v): (*xy(u), *xy(v)) for u, v, _ in edges}
        cuts = {}      # (u, v) -> {point key: (t, x, y)}
        links = []     # (dead-end node, target node key) for joined dead ends
        pts = {}       # key -> (x, y) for every new node

        def add_cut(edge, t, x, y):
            k = pkey(x, y)
            cuts.setdefault(edge, {})[k] = (t, x, y)
            pts[k] = (x, y)
            return k

        # 1) crossings without a junction
        for i in range(len(edges)):
            u1, v1, l1 = edges[i]
            x1, y1, x2, y2 = geo[(u1, v1)]
            for j in range(i + 1, len(edges)):
                u2, v2, l2 = edges[j]
                if l1 != l2 or {u1, v1} & {u2, v2}:
                    continue
                x3, y3, x4, y4 = geo[(u2, v2)]
                if max(x1, x2) < min(x3, x4) or max(x3, x4) < min(x1, x2) \
                   or max(y1, y2) < min(y3, y4) or max(y3, y4) < min(y1, y2):
                    continue
                d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
                if abs(d) < 1e-9:
                    continue
                t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
                s = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
                if 0 < t < 1 and 0 < s < 1:
                    px, py = x1 + t * (x2 - x1), y1 + t * (y2 - y1)
                    add_cut((u1, v1), t, px, py)
                    add_cut((u2, v2), s, px, py)

        # 2) dead ends that stop just short of another path
        for n in [n for n in G.nodes if G.degree(n) == 1]:
            nb = next(iter(G[n]))
            layer = G[n][nb].get("layer", 0)
            px, py = xy(n)
            best = None
            for (u, v, l) in edges:
                if l != layer or n in (u, v) or nb in (u, v):
                    continue
                x1, y1, x2, y2 = geo[(u, v)]
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
                qx, qy = x1 + t * dx, y1 + t * dy
                dist = math.hypot(px - qx, py - qy)
                if dist <= JOIN_GAP_M and (best is None or dist < best[0]):
                    best = (dist, (u, v), t, qx, qy, math.sqrt(L2))
            if best:
                dist, e, t, qx, qy, L = best
                if t * L < 0.5:
                    links.append((n, e[0]))
                elif (1 - t) * L < 0.5:
                    links.append((n, e[1]))
                else:
                    links.append((n, add_cut(e, t, qx, qy)))

        # apply: split cut edges into pieces, then add the dead-end joins
        def latlng(k):
            if k in G.nodes:
                return G.nodes[k]["lat"], G.nodes[k]["lng"]
            x, y = pts[k]
            return y / ky, x / kx

        for (u, v), cs in cuts.items():
            attrs = G[u][v]
            layer, car, ow = attrs.get("layer", 0), attrs.get("car", False), attrs.get("oneway_from")
            G.remove_edge(u, v)
            chain = [u] + [k for k, _ in sorted(cs.items(), key=lambda kv: kv[1][0])] + [v]
            if ow == v:                       # keep the chain in the one-way driving direction
                chain = chain[::-1]
            for a, b in zip(chain, chain[1:]):
                for k in (a, b):
                    if k not in G:
                        la, ln = latlng(k)
                        G.add_node(k, lat=la, lng=ln)
                if a != b:
                    G.add_edge(a, b, distance=haversine(*latlng(a), *latlng(b)), layer=layer,
                               car=car, oneway_from=(a if ow else None))
        for n, target in links:
            if n != target and not G.has_edge(n, target):
                # joins of small gaps are for walking only
                G.add_edge(n, target, distance=haversine(*latlng(n), *latlng(target)),
                           layer=G[n][next(iter(G[n]))].get("layer", 0), car=False, oneway_from=None)
        return G

    # projection helpers
    def _xy(self, lat, lng):
        return (lng - self._lng0) * self._kx, (lat - self._lat0) * self._ky

    def _latlng(self, x, y):
        return self._lat0 + y / self._ky, self._lng0 + x / self._kx

    # parking areas (amenity=parking in the .osm file)
    def _find_parkings(self, osm):
        # Every amenity=parking area/point that a car can reach.
        found = []
        way_ids = osm.get("way_ids") or [None] * len(osm["ways"])
        for wid, (refs, tags) in zip(way_ids, osm["ways"]):
            if tags.get("amenity") != "parking":
                continue
            pts = list(dict.fromkeys(r for r in refs if r in osm["nodes"]))  # drop the repeated closing node
            if not pts:
                continue
            lat = sum(osm["nodes"][r][0] for r in pts) / len(pts)
            lng = sum(osm["nodes"][r][1] for r in pts) / len(pts)
            found.append((f"w{wid}", tags, lat, lng))
        for nid, tags in osm["node_tags"].items():
            if tags.get("amenity") == "parking":
                found.append((f"n{nid}", tags, *osm["nodes"][nid]))

        named = [(t.get("name"), *osm["nodes"][n]) for n, t in osm["node_tags"].items()
                 if t.get("name") and ("alt_name" in t or t.get("barrier") in GATE_BARRIERS)]
        out = []
        for i, (pid, tags, lat, lng) in enumerate(found):
            name = tags.get("name")
            try:
                capacity = int(tags.get("capacity")) if tags.get("capacity") else None
            except ValueError:
                capacity = None
            if not self._car_segments:
                break
            car = self.snap(lat, lng, car=True)
            if car.offset_m > 150:            # no road anywhere near it
                continue
            if not name and named:
                near = min(named, key=lambda b: haversine(lat, lng, b[1], b[2]))
                name = f"Parking near {near[0]}"
            name = (name or f"Parking {i + 1}").strip()
            name = name[0].upper() + name[1:]
            out.append({"id": pid if pid != "wNone" else f"p{i + 1}", "name": name, "lat": lat, "lng": lng,
                        "capacity": capacity, "car_snap": car, "walk_snap": self.snap(lat, lng)})
        return out

    def parking_info(self, P, full_ids=()):
        # The public part of a parking area (what the page and the route replies show).
        return {"id": P["id"], "name": P["name"], "lat": round(P["lat"], 7), "lng": round(P["lng"], 7),
                "capacity": P.get("capacity"), "full": P["id"] in set(full_ids or ())}

    # snapping
    def snap(self, lat, lng, car=False):
        # Closest point on ANY path segment (or, with car=True, on any drivable road).
        px, py = self._xy(lat, lng)
        best = None
        for u, v, x1, y1, x2, y2 in (self._car_segments if car else self._segments):
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

    # routing
    def _allowed(self, u, v, directed):
        # (u->v allowed, v->u allowed) along a segment.
        if not directed:
            return True, True
        return self.D.has_edge(u, v), self.D.has_edge(v, u)

    def blocked_from_points(self, points):
        # the path pieces to treat as closed for each avoid point, and where on each piece it lies
        out = {}
        for lat, lng in points or []:
            sn = self.snap(lat, lng)
            if sn.offset_m <= AVOID_SNAP_M:
                out.setdefault(frozenset((sn.u, sn.v)), []).append((sn.u, sn.t))
        return out

    def _prepare(self, base, s, e, directed, blocked=None):
        # Copy of the graph with the start/end points spliced into their segments.
        blocked = blocked or {}
        H = base.copy()  # tiny graph, so a copy per request is cheap
        H.add_node("__start__", lat=s.lat, lng=s.lng)
        H.add_node("__end__", lat=e.lat, lng=e.lng)

        L = self.G[s.u][s.v]["distance"]
        fwd, back = self._allowed(s.u, s.v, directed)
        if fwd:
            H.add_edge("__start__", s.v, distance=(1 - s.t) * L)
        if back:
            H.add_edge("__start__", s.u, distance=s.t * L)

        L2 = self.G[e.u][e.v]["distance"]
        fwd, back = self._allowed(e.u, e.v, directed)
        if fwd:
            H.add_edge(e.u, "__end__", distance=e.t * L2)
        if back:
            H.add_edge(e.v, "__end__", distance=(1 - e.t) * L2)

        if {s.u, s.v} == {e.u, e.v}:          # both on the same segment: go straight along it
            et = e.t if e.u == s.u else 1 - e.t
            fwd, back = self._allowed(s.u, s.v, directed)
            if (fwd and et >= s.t) or (back and et <= s.t):
                H.add_edge("__start__", "__end__", distance=abs(et - s.t) * L)

        # costs: closed stretches become very expensive
        for _, _, d in H.edges(data=True):
            d["cost"] = d["distance"]

        def ts_on(seg_u, seg_v, ref_u):
            # blocked positions on segment seg_u-seg_v, as t measured from ref_u
            return [t if u == ref_u else 1 - t for u, t in blocked.get(frozenset((seg_u, seg_v)), [])]

        def block(a, b):
            if H.has_edge(a, b):
                H[a][b]["cost"] = H[a][b]["distance"] * AVOID_FACTOR + 50

        for key in blocked:
            u, v = tuple(key)
            block(u, v)
            if directed:
                block(v, u)
        # pieces of the start / end segments: block only the side where the closed point is
        for t in ts_on(s.u, s.v, s.u):
            block("__start__", s.v) if t >= s.t else block("__start__", s.u)
            if H.has_edge("__start__", "__end__"):
                et = e.t if e.u == s.u else 1 - e.t
                if min(s.t, et) <= t <= max(s.t, et):
                    block("__start__", "__end__")
        for t in ts_on(e.u, e.v, e.u):
            block(e.u, "__end__") if t <= e.t else block(e.v, "__end__")
        return H

    @staticmethod
    def _blocked_m(H, nodes):
        # How many metres of a path run through closed stretches.
        return sum(H[a][b]["distance"] for a, b in zip(nodes, nodes[1:])
                   if H[a][b].get("cost", 0) > H[a][b]["distance"] * 1.5)

    @staticmethod
    def _length(H, nodes):
        return sum(H[a][b]["distance"] for a, b in zip(nodes, nodes[1:]))

    @staticmethod
    def _coords(H, nodes):
        return [[H.nodes[n]["lat"], H.nodes[n]["lng"]] for n in nodes]

    def _candidate_paths(self, H, k=3):
        # The shortest path first, then up to k-1 clearly DIFFERENT alternatives.
        best = nx.shortest_path(H, "__start__", "__end__", weight="cost")
        base = self._length(H, best)
        out, last = [best], best
        for _, _, d in H.edges(data=True):
            d["w"] = d["cost"]
        for _ in range(8):
            if len(out) >= k:
                break
            for a, b in zip(last, last[1:]):
                H[a][b]["w"] *= ALT_PENALTY
                if H.is_directed() and H.has_edge(b, a):
                    H[b][a]["w"] *= ALT_PENALTY
            try:
                p = nx.shortest_path(H, "__start__", "__end__", weight="w")
            except nx.NetworkXNoPath:
                break
            last = p
            length = self._length(H, p)
            if length > base * ALT_MAX_RATIO + ALT_SLACK_M:
                break                           # every remaining detour is longer still
            if self._blocked_m(H, p) > self._blocked_m(H, best):
                continue                        # never offer a way through a closed stretch
            if all(self._overlap(H, p, q) <= ALT_MAX_OVERLAP for q in out):
                out.append(p)
        return out

    def _overlap(self, H, p, q):
        # Share (0..1) of path p's length that it has in common with path q.
        qe = {frozenset(e) for e in zip(q, q[1:])}
        total = self._length(H, p) or 1.0
        shared = sum(H[a][b]["distance"] for a, b in zip(p, p[1:]) if frozenset((a, b)) in qe)
        return shared / total

    @staticmethod
    def _seg_overlap(c1, c2):
        # Same as _overlap, for plain coordinate lists (used for car + walk routes).
        key = lambda a, b: frozenset(((round(a[0], 6), round(a[1], 6)), (round(b[0], 6), round(b[1], 6))))
        s2 = {key(a, b) for a, b in zip(c2, c2[1:])}
        total = shared = 0.0
        for a, b in zip(c1, c1[1:]):
            d = haversine(a[0], a[1], b[0], b[1])
            total += d
            if key(a, b) in s2:
                shared += d
        return shared / (total or 1.0)

    def walk_routes(self, start, end, k=3, avoid=None):
        # Walking only.
        s, e = self.snap(*start), self.snap(*end)
        H = self._prepare(self.G, s, e, directed=False, blocked=self.blocked_from_points(avoid))
        routes = []
        for nodes in self._candidate_paths(H, k):
            path_m = self._length(H, nodes)
            coords = self._coords(H, nodes)
            total = path_m + s.offset_m + e.offset_m
            routes.append({
                "mode": "walk",
                "legs": [{"mode": "walk", "coordinates": coords, "distance_m": round(path_m, 1)}],
                "coordinates": coords,
                "start_connector": [[start[0], start[1]], [s.lat, s.lng]],
                "end_connector": [[e.lat, e.lng], [end[0], end[1]]],
                "distance_meters": round(total, 1),
                "walk_m": round(total, 1),
                "drive_m": 0,
                "time_min": round(total / WALK_M_PER_MIN, 1),
                "estimated_walk_time_min": round(total / WALK_M_PER_MIN, 1),
                "parking": None,
                "through_closed_m": round(self._blocked_m(H, nodes), 1),
                "start_offset_m": round(s.offset_m, 1),
                "end_offset_m": round(e.offset_m, 1),
            })
        return routes

    def route(self, start, end):
        # The single shortest walking route (kept for older callers and the start-up checks).
        return self.walk_routes(start, end, k=1)[0]

    def car_routes(self, start, end, k=3, avoid=None, full_ids=(), skip_ids=()):
        # Car + walk: drive on the roads to a parking area, park, then walk to the building.
        if not self.parkings or self.D.number_of_nodes() == 0:
            raise nx.NetworkXNoPath("No parking areas in the map.")
        full_ids = set(full_ids or ())
        s = self.snap(*start, car=True)
        e = self.snap(*end)
        blocked = self.blocked_from_points(avoid)

        options = []
        for P in self.parkings:
            try:
                HD = self._prepare(self.D, s, P["car_snap"], directed=True, blocked=blocked)
                drives = self._candidate_paths(HD, k)
                HW = self._prepare(self.G, P["walk_snap"], e, directed=False, blocked=blocked)
                walk_nodes = nx.shortest_path(HW, "__start__", "__end__", weight="cost")
            except nx.NetworkXNoPath:
                continue
            walk_path_m = self._length(HW, walk_nodes)
            walk_coords = [[P["lat"], P["lng"]]] + self._coords(HW, walk_nodes)
            walk_m = s.offset_m + P["walk_snap"].offset_m + walk_path_m + e.offset_m
            for nodes in drives:
                drive_coords = self._coords(HD, nodes) + [[P["lat"], P["lng"]]]
                drive_m = self._length(HD, nodes) + P["car_snap"].offset_m
                minutes = drive_m / DRIVE_M_PER_MIN + PARKING_MIN + walk_m / WALK_M_PER_MIN
                closed_m = self._blocked_m(HD, nodes) + self._blocked_m(HW, walk_nodes)
                options.append((minutes + closed_m, {
                    "mode": "car",
                    "legs": [
                        {"mode": "drive", "coordinates": drive_coords, "distance_m": round(drive_m, 1)},
                        {"mode": "walk", "coordinates": walk_coords,
                         "distance_m": round(P["walk_snap"].offset_m + walk_path_m, 1)},
                    ],
                    "coordinates": drive_coords + walk_coords[1:],
                    "start_connector": [[start[0], start[1]], [s.lat, s.lng]],
                    "end_connector": [[e.lat, e.lng], [end[0], end[1]]],
                    "distance_meters": round(drive_m + walk_m, 1),
                    "walk_m": round(walk_m, 1),
                    "drive_m": round(drive_m, 1),
                    "drive_min": round(drive_m / DRIVE_M_PER_MIN, 1),
                    "walk_min": round(walk_m / WALK_M_PER_MIN, 1),
                    "time_min": round(minutes, 1),
                    "estimated_walk_time_min": round(walk_m / WALK_M_PER_MIN, 1),
                    "parking": self.parking_info(P, full_ids),
                    "through_closed_m": round(closed_m, 1),
                    "start_offset_m": round(s.offset_m, 1),
                    "end_offset_m": round(e.offset_m, 1),
                }))
        if not options:
            raise nx.NetworkXNoPath("No parking area can be reached by car.")

        options.sort(key=lambda o: o[0])
        skip_ids = set(skip_ids or ())
        free = [o for o in options if not o[1]["parking"]["full"] and o[1]["parking"]["id"] not in skip_ids]
        all_full = not free
        not_tried = [o for o in options if o[1]["parking"]["id"] not in skip_ids]
        usable = free or not_tried or options
        best_time = usable[0][0]
        # full parkings that would have been quicker than the one we now send the student to
        skipped, seen = [], set()
        if not all_full:
            for minutes, r in options:
                P = r["parking"]
                if minutes >= best_time:
                    break
                if P["full"] and P["id"] not in seen:
                    seen.add(P["id"])
                    skipped.append({"id": P["id"], "name": P["name"]})

        kept = []
        for minutes, r in usable:
            if len(kept) >= k:
                break
            if kept and minutes > best_time * ALT_MAX_RATIO + 3:
                break
            if all(self._seg_overlap(r["coordinates"], q["coordinates"]) <= ALT_MAX_OVERLAP for q in kept):
                kept.append(r)
        # when a better parking is full, make sure another free parking is offered as well
        # (not only other ways to drive to the same one), so the student has a real choice
        if skipped and len(kept) < k:
            used = {r["parking"]["id"] for r in kept}
            for minutes, r in usable:
                if r["parking"]["id"] not in used:
                    kept.append(r)
                    break
        for r in kept:
            r["full_skipped"] = skipped
            r["all_parkings_full"] = all_full
        return kept

    def nearest_parking(self, end, full_ids=()):
        # The parking area closest (on foot) to a destination, preferring ones with space.
        if not self.parkings:
            return None
        full_ids = set(full_ids or ())
        e = self.snap(*end)

        def walk_m(P):
            try:
                H = self._prepare(self.G, P["walk_snap"], e, directed=False)
                return self._length(H, nx.shortest_path(H, "__start__", "__end__", weight="distance")) \
                    + P["walk_snap"].offset_m + e.offset_m
            except nx.NetworkXNoPath:
                return float("inf")

        ranked = sorted(((walk_m(P), P) for P in self.parkings), key=lambda x: x[0])
        free = [x for x in ranked if x[1]["id"] not in full_ids]
        m, P = (free or ranked)[0]
        info = self.parking_info(P, full_ids)
        info["walk_m"] = round(m, 1) if m != float("inf") else None
        info["skipped_full"] = [self.parking_info(Q, full_ids)["name"] for mm, Q in ranked
                                if Q["id"] in full_ids and mm < m] if free else []
        return info

    def drive_routes(self, start, end, k=3, avoid=None, full_ids=()):
        # Car only: drive on the roads all the way to the point of road closest to the
        # destination (drop-off / pick-up point).
        if self.D.number_of_nodes() == 0:
            raise nx.NetworkXNoPath("No drivable roads in the map.")
        s = self.snap(*start, car=True)
        e = self.snap(*end, car=True)
        H = self._prepare(self.D, s, e, directed=True, blocked=self.blocked_from_points(avoid))
        nearby = self.nearest_parking(end, full_ids)
        routes = []
        for nodes in self._candidate_paths(H, k):
            coords = self._coords(H, nodes)
            drive_m = self._length(H, nodes) + s.offset_m
            walk_m = e.offset_m                     # road -> building door
            minutes = drive_m / DRIVE_M_PER_MIN + walk_m / WALK_M_PER_MIN
            routes.append({
                "mode": "drive",
                "legs": [{"mode": "drive", "coordinates": coords, "distance_m": round(drive_m, 1)}],
                "coordinates": coords,
                "start_connector": [[start[0], start[1]], [s.lat, s.lng]],
                "end_connector": [[e.lat, e.lng], [end[0], end[1]]],
                "distance_meters": round(drive_m + walk_m, 1),
                "walk_m": round(walk_m, 1),
                "drive_m": round(drive_m, 1),
                "drive_min": round(drive_m / DRIVE_M_PER_MIN, 1),
                "walk_min": round(walk_m / WALK_M_PER_MIN, 1),
                "time_min": round(minutes, 1),
                "estimated_walk_time_min": round(walk_m / WALK_M_PER_MIN, 1),
                "parking": None,
                "drop_off": {"lat": round(e.lat, 7), "lng": round(e.lng, 7), "to_door_m": round(walk_m, 1)},
                "nearby_parking": nearby,
                "through_closed_m": round(self._blocked_m(H, nodes), 1),
                "start_offset_m": round(s.offset_m, 1),
                "end_offset_m": round(e.offset_m, 1),
            })
        return routes

    # sanity checks (printed when the server starts)
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


# CLI: write buildings.json

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
