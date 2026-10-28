"""Snap, node and polygonize CAD linework into candidate faces; gaps and dangles are reported, never closed silently."""
from __future__ import annotations

import numpy as np
import shapely
from shapely import STRtree

ON_EDGE = 1e-6  # metres: an edge midpoint this close to a source line came from it
SAME_EDGE_RATIO = 3


class Joins:
    """Union-find over endpoint indices."""

    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def as_lines(coordinate_arrays):
    """An object array of LineStrings; the arrays may have different lengths."""
    return np.array([shapely.linestrings(c) for c in coordinate_arrays], dtype=object)


def insert_vertex(coords, point):
    """coords with the point on the line nearest to `point` inserted as a vertex; returns (coords, inserted point)."""
    line = shapely.linestrings(coords)
    distance = shapely.line_locate_point(line, shapely.points(point))
    lengths = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(coords, axis=0).T))])
    k = int(min(max(np.searchsorted(lengths, distance, side='right'), 1), len(coords)-1))
    p = np.array(shapely.get_coordinates(shapely.line_interpolate_point(line, distance))[0])
    for existing in (coords[k-1], coords[k]):
        if np.allclose(existing, p, rtol=0, atol=1e-12):
            return coords, existing.copy()
    return np.insert(coords, k, p, axis=0), p


def snap(lines, tolerance):
    """Merge endpoints within `tolerance`, then move remaining free endpoints onto lines within `tolerance`.

    Returns new coordinate arrays and the moves made (kind, line index, distance)."""
    lines = [np.array(c, dtype=float) for c in lines]
    ends, owner = [], []
    for i, coords in enumerate(lines):
        if len(coords) >= 2 and not np.array_equal(coords[0], coords[-1]):
            ends += [coords[0], coords[-1]]
            owner += [(i, 0), (i, -1)]
    moves = []
    if not ends:
        return lines, moves
    points = shapely.points(np.array(ends))
    joins = Joins(len(ends))
    left, right = STRtree(points).query(points, predicate='dwithin', distance=tolerance)
    for a, b in zip(left, right):
        if a < b:
            joins.union(int(a), int(b))
    clusters = {}
    for i in range(len(ends)):
        clusters.setdefault(joins.find(i), []).append(i)
    single = []
    for members in clusters.values():
        if len(members) == 1:
            single.append(members[0])
            continue
        target = np.mean([ends[i] for i in members], axis=0)
        for i in members:
            line, at = owner[i]
            moved = float(np.hypot(*(lines[line][at] - target)))
            if moved > 0:
                moves.append(('endpoint', line, moved))
            lines[line][at] = target
    # One tree for every free end: inserted vertices do not change a line's shape, and a moved end moves by
    # at most `tolerance`, so the geometries stay good enough to find the nearest line.
    geometries = as_lines(lines)
    tree = STRtree(geometries)
    for i in single:
        line, at = owner[i]
        point = lines[line][at]
        near = tree.query(shapely.points(point), predicate='dwithin', distance=tolerance)
        near = [j for j in near if j != line]
        if not near:
            continue
        distances = shapely.distance(geometries[near], shapely.points(point))
        target = int(near[int(np.argmin(distances))])
        lines[target], p = insert_vertex(lines[target], point)
        moved = float(np.hypot(*(point - p)))
        lines[line][at] = p
        if moved > 0:  # an end already on the line still gets the shared vertex, but did not move
            moves.append(('to-line', line, moved))
    return lines, moves


def faces(linework):
    """Node the linework, then polygonize: (edges, faces, dangles, cut edges, invalid rings)."""
    noded = shapely.union_all(linework)
    edges = shapely.get_parts(noded)
    edges = edges[shapely.length(edges) > 0]
    polygons, cuts, dangles, invalid = shapely.polygonize_full(edges)
    return edges, *(shapely.get_parts(g) for g in (polygons, cuts, dangles, invalid))


def free_ends(edges):
    """Edge end coordinates used by exactly one edge, with the index of that edge."""
    counts, first = {}, {}
    for i, edge in enumerate(edges):
        coords = shapely.get_coordinates(edge)
        for point in (tuple(coords[0]), tuple(coords[-1])):
            counts[point] = counts.get(point, 0) + 1
            first.setdefault(point, i)
    return [(np.array(p), first[p]) for p, n in counts.items() if n == 1]


def gaps(edges, max_gap):
    """Pairs of free ends, or a free end and another edge, closer than `max_gap`: (line, kind, edge a, edge b)."""
    ends = free_ends(edges)
    if not ends:
        return []
    points = shapely.points(np.array([p for p, _ in ends]))
    found, paired = [], set()
    tree = STRtree(points)
    edge_tree = STRtree(edges)
    for i, (point, edge) in enumerate(ends):
        if i in paired:
            continue
        near = [j for j in tree.query(points[i], predicate='dwithin', distance=max_gap) if j != i and j not in paired]
        # Both ends of one short stray edge are not a gap: keep a same-edge pair only when the way round
        # along the edge is much longer than the gap itself.
        near = [j for j in near if ends[j][1] != edge or
                shapely.length(edges[edge]) > SAME_EDGE_RATIO*shapely.distance(points[i], points[j])]
        if near:
            j = near[int(np.argmin(shapely.distance(points[near], points[i])))]
            paired.update((i, j))
            found.append((shapely.linestrings([point, ends[j][0]]), 'endpoint', edge, ends[j][1]))
            continue
        near = [k for k in edge_tree.query(points[i], predicate='dwithin', distance=max_gap) if k != edge]
        near = [k for k in near if shapely.distance(edges[k], points[i]) > 0]
        if near:
            k = near[int(np.argmin(shapely.distance(edges[near], points[i])))]
            other = shapely.line_interpolate_point(edges[k], shapely.line_locate_point(edges[k], points[i]))
            found.append((shapely.linestrings([point, shapely.get_coordinates(other)[0]]), 'to-line', edge, k))
    return found


def sources(geometries, lines, labels):
    """For each geometry, the sorted labels of source lines its midpoint lies on (within ON_EDGE)."""
    tree = STRtree(lines)
    result = []
    for geometry in geometries:
        mid = shapely.line_interpolate_point(geometry, 0.5, normalized=True)
        hits = tree.query(mid, predicate='dwithin', distance=ON_EDGE)
        result.append(sorted({labels[j] for j in hits if labels[j]}))
    return result


def boundary_labels(face, edges, edge_labels, edge_tree):
    """Labels (handles or layers) of the edges that make up a face's boundary."""
    ring = shapely.boundary(face)
    labels = set()
    for k in edge_tree.query(ring, predicate='intersects'):
        mid = shapely.line_interpolate_point(edges[k], 0.5, normalized=True)
        if shapely.distance(ring, mid) <= ON_EDGE:
            labels.update(edge_labels[k])
    return sorted(labels)
