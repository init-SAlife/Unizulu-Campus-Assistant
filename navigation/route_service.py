# runs route calculations in parallel, safely, for many users at once.

import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool

import networkx as nx

from navigation.campus_graph import Campus


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


WORKERS = _env_int("ROUTE_WORKERS", min(os.cpu_count() or 2, 8))
MAX_PENDING = _env_int("ROUTE_MAX_PENDING", max(WORKERS, 1) * 16)
QUEUE_WAIT_S = _env_int("ROUTE_QUEUE_WAIT_S", 5)
JOB_TIMEOUT_S = _env_int("ROUTE_JOB_TIMEOUT_S", 15)
CACHE_SIZE = _env_int("ROUTE_CACHE_SIZE", 2000)


class ServerBusy(Exception):
    # Too many route requests at the same moment: the client should retry shortly.
    pass


# inside each worker process
_WORKER_CAMPUS = None


def _init_worker():
    # Runs once when a worker process starts: load the map into that process.
    global _WORKER_CAMPUS
    _WORKER_CAMPUS = Campus()


def _warm_up():
    time.sleep(0.3)          # long enough that every worker gets one of the warm-up jobs
    return os.getpid()


def compute_routes(campus, mode, start, end, avoid=(), full=(), skip=()):
    # The actual work.
    try:
        if mode == "car":
            routes = campus.car_routes(start, end, k=3, avoid=avoid, full_ids=full, skip_ids=skip)
            try:
                walk_only = campus.walk_routes(start, end, k=1, avoid=avoid)[0]["time_min"]
            except nx.NetworkXNoPath:
                walk_only = None
        elif mode == "drive":
            routes = campus.drive_routes(start, end, k=3, avoid=avoid, full_ids=full)
            walk_only = None
        else:
            routes = campus.walk_routes(start, end, k=3, avoid=avoid)
            walk_only = None
        return {"ok": True, "routes": routes, "walk_only_min": walk_only}
    except nx.NetworkXNoPath:
        return {"ok": False, "reason": "no_path"}


def _worker_job(mode, start, end, avoid=(), full=(), skip=()):
    return compute_routes(_WORKER_CAMPUS, mode, start, end, avoid, full, skip)


# in the web server process
class RouteService:
    def __init__(self, local_campus, workers=WORKERS):
        self.local = local_campus             # used when there are no workers, or as a fallback
        self.workers = max(0, workers)
        self._pool = None
        self._pool_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_PENDING)
        self._cache = OrderedDict()
        self._cache_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.stats = {"requests": 0, "cache_hits": 0, "busy_rejected": 0, "in_flight": 0,
                      "worker_restarts": 0, "fallback_local": 0, "max_in_flight": 0}

    # process pool (created on first use, so it is never copied by fork/reloader)
    def _get_pool(self):
        with self._pool_lock:
            if self._pool is None and self.workers > 0:
                self._pool = ProcessPoolExecutor(max_workers=self.workers, initializer=_init_worker)
            return self._pool

    def _reset_pool(self, broken):
        # Replace a crashed pool -- once, even if many requests notice the crash together.
        with self._pool_lock:
            if self._pool is not broken:
                return                        # another thread already replaced it
            print("[navigator] a route worker crashed; restarting the worker pool")
            broken.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        self._bump("worker_restarts")

    def warm_up(self):
        # Start the workers in the background so the first users do not wait for them.
        def run():
            pool = self._get_pool()
            if pool is None:
                return
            try:
                pids = {f.result(timeout=60) for f in [pool.submit(_warm_up) for _ in range(self.workers * 2)]}
                print(f"[navigator] {len(pids)} route worker process(es) ready")
            except Exception as exc:
                print(f"[navigator] route workers could not start ({exc}); calculating in-process")
        threading.Thread(target=run, daemon=True, name="route-warmup").start()

    def shutdown(self):
        with self._pool_lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

    # bookkeeping
    def _bump(self, key, n=1):
        with self._stats_lock:
            self.stats[key] += n
            if key == "in_flight":
                self.stats["max_in_flight"] = max(self.stats["max_in_flight"], self.stats["in_flight"])

    def snapshot(self):
        with self._stats_lock:
            s = dict(self.stats)
        with self._cache_lock:
            s["cache_entries"] = len(self._cache)
        s.update({"workers": self.workers, "max_pending": MAX_PENDING})
        return s

    # cache
    @staticmethod
    def _key(mode, start, end, avoid=(), full=(), skip=()):
        # 5 decimals = about 1 m: two people standing in the same spot share a route.
        return (mode, round(start[0], 5), round(start[1], 5), round(end[0], 5), round(end[1], 5),
                tuple((round(a, 5), round(b, 5)) for a, b in avoid),
                tuple(sorted(full)) if mode != "walk" else (),
                tuple(sorted(skip)) if mode != "walk" else ())

    def _cache_get(self, key):
        with self._cache_lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def _cache_put(self, key, value):
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)

    # the public call
    def routes(self, mode, start, end, avoid=(), full=(), skip=()):
        # returns the routes (or ok False); full = ids of parkings reported full
        self._bump("requests")
        avoid = tuple(tuple(p) for p in (avoid or ()))
        full = tuple(sorted(full or ())) if mode != "walk" else ()
        skip = tuple(sorted(skip or ())) if mode != "walk" else ()
        key = self._key(mode, start, end, avoid, full, skip)
        hit = self._cache_get(key)
        if hit is not None:
            self._bump("cache_hits")
            return hit

        if not self._slots.acquire(timeout=QUEUE_WAIT_S):
            self._bump("busy_rejected")
            raise ServerBusy()
        self._bump("in_flight")
        try:
            result = self._run(mode, start, end, avoid, full, skip)
        finally:
            self._bump("in_flight", -1)
            self._slots.release()

        if result.get("ok") or result.get("reason") == "no_path":
            self._cache_put(key, result)
        return result

    def _run(self, mode, start, end, avoid=(), full=(), skip=()):
        pool = self._get_pool()
        if pool is None:
            return compute_routes(self.local, mode, start, end, avoid, full, skip)
        try:
            return pool.submit(_worker_job, mode, start, end, avoid, full, skip).result(timeout=JOB_TIMEOUT_S)
        except BrokenProcessPool:
            self._reset_pool(pool)
        except FutureTimeout:
            print("[navigator] a route calculation timed out in a worker")
        # fallback: answer the user from this process (the map is loaded here too)
        self._bump("fallback_local")
        return compute_routes(self.local, mode, start, end, avoid, full, skip)


_started = time.time()


def uptime_s():
    return round(time.time() - _started)
