#!/usr/bin/env python3
"""Light concurrency probe: N clients x M requests through the router (and the gateway behind it)
to the local stub. Reports p50/p95/max latency and failures; exits 1 on any failure.

Not a load test: the defaults (30 clients x 5 requests = 150 requests) mirror 30 concurrent
sessions each with a few calls in flight. The stub answers instantly, so the numbers measure the
router and gateway overhead and the thread model, never model latency.
"""
import argparse
import http.client
import json
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit


def one_request(parts, session):
    body = json.dumps({"model": "probe", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body)),
               "X-Forge-User": "probe", "X-Forge-Session": session}
    started = time.monotonic()
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    try:
        conn.request("POST", "/v1/chat/completions", body, headers)
        resp = conn.getresponse()
        data = resp.read()
        ok = resp.status == 200 and json.loads(data).get("choices") is not None
    except (OSError, ValueError):
        ok = False
    finally:
        conn.close()
    return ok, time.monotonic() - started


def run(url, clients, requests):
    parts = urlsplit(url)
    latencies, failures, lock = [], [0], threading.Lock()
    barrier = threading.Barrier(clients)

    def client(index):
        barrier.wait()
        for n in range(requests):
            ok, took = one_request(parts, "probe-%d-%d" % (index, n))
            with lock:
                latencies.append(took)
                if not ok:
                    failures[0] += 1

    threads = [threading.Thread(target=client, args=(i,)) for i in range(clients)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return latencies, failures[0], time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:4000")
    parser.add_argument("--clients", type=int, default=30)
    parser.add_argument("--requests", type=int, default=5)
    args = parser.parse_args()
    latencies, failures, wall = run(args.url, args.clients, args.requests)
    latencies.sort()
    total = len(latencies)
    p95 = latencies[min(total - 1, int(total * 0.95))]
    print("probe: %d requests (%d clients x %d), %d failed, wall %.2fs" % (total, args.clients, args.requests, failures, wall))
    print("probe: latency p50 %.0f ms, p95 %.0f ms, max %.0f ms" % (
        statistics.median(latencies) * 1000, p95 * 1000, latencies[-1] * 1000))
    return 1 if failures or total != args.clients * args.requests else 0


if __name__ == "__main__":
    sys.exit(main())
