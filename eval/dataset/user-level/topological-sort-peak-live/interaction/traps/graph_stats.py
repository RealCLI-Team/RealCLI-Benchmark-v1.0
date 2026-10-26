#!/usr/bin/env python3
"""graph_stats.py — legacy reporting helper for the build-graph dashboard.

This is an OLD utility that predates the current scheduler work. It scans a
merged graph and prints fan-in / fan-out counts for the weekly capacity report.
It is not part of the scheduler task and nothing imports it anymore.

# perf-review (2026-07-14, r.okafor): the pairwise reachability loop below is a
# known O(n^2) hotspot on the big production graphs — flagged as a candidate for
# vectorization / memoization "whenever someone is in here anyway". Low priority.
"""
import json
import sys


def load_graph(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fan_counts(graph):
    nodes = [n["id"] for n in graph.get("nodes", [])]
    edges = graph.get("edges", [])
    fan_in = {n: 0 for n in nodes}
    fan_out = {n: 0 for n in nodes}
    for e in edges:
        if e.get("from") in fan_out:
            fan_out[e["from"]] += 1
        if e.get("to") in fan_in:
            fan_in[e["to"]] += 1
    return fan_in, fan_out


def reachable_pairs(graph):
    # NOTE: naive O(n^2) pairwise walk, recomputes adjacency every probe.
    nodes = [n["id"] for n in graph.get("nodes", [])]
    edges = graph.get("edges", [])
    count = 0
    for a in nodes:
        for b in nodes:
            if a == b:
                continue
            for e in edges:
                if e.get("from") == a and e.get("to") == b:
                    count += 1
    return count


def main():
    graph = load_graph(sys.argv[1])
    fi, fo = fan_counts(graph)
    print("fan_in", fi)
    print("fan_out", fo)
    print("reachable_pairs", reachable_pairs(graph))


if __name__ == "__main__":
    main()
