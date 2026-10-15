"""Deterministic graph layout and orthogonal wire routing, independent of Tk."""

from __future__ import annotations

from collections import defaultdict
import heapq
import math

from formula_document import FormulaDocument

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


def block_size(block: dict) -> tuple[float, float]:
    return {
        "area": (42.0, 42.0),
        "constant": (66.0, 36.0),
        "operation": (88.0, 64.0),
        "output": (104.0, 44.0),
    }.get(block.get("kind"), (100.0, 44.0))


def connections(blocks: dict[int, dict]):
    """Yield actual source/target/slot links without changing incomplete graphs."""
    for target, block in sorted(blocks.items()):
        sources = (
            block.get("inputs", [])
            if block.get("kind") == "operation"
            else [block.get("input")]
            if block.get("kind") == "output"
            else []
        )
        for index, source in enumerate(sources):
            if isinstance(source, int) and source in blocks:
                yield source, target, index


def _layers(nodes, edges, order):
    """Condense strongly connected components so even invalid cyclic graphs lay out."""
    children, parents = defaultdict(set), defaultdict(set)
    for source, target in edges:
        if source != target:
            children[source].add(target)
            parents[target].add(source)
    seen, finished = set(), []
    for root in sorted(nodes, key=order.get):
        stack = [(root, False)]
        while stack:
            node, closing = stack.pop()
            if closing:
                finished.append(node)
            elif node not in seen:
                seen.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in sorted(children[node], key=order.get))
    component, groups = {}, []
    for root in reversed(finished):
        if root in component:
            continue
        group, stack = [], [root]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component[node] = len(groups)
            group.append(node)
            stack.extend(parents[node])
        groups.append(group)
    ranks = [0] * len(groups)
    # Kosaraju's component order is topological.
    for cid, group in enumerate(groups):
        ranks[cid] = max(
            (ranks[component[p]] + 1 for n in group for p in parents[n] if component[p] != cid),
            default=0,
        )
    layers = defaultdict(list)
    for node in nodes:
        layers[ranks[component[node]]].append(node)
    layers = [sorted(layers[rank], key=order.get) for rank in sorted(layers)]
    # Alternating barycentre passes reduce wire crossings without disturbing ties.
    for forward in (True, False, True, False):
        positions = {node: index for layer in layers for index, node in enumerate(layer)}
        indices = range(1, len(layers)) if forward else range(len(layers) - 2, -1, -1)
        for index in indices:
            neighbours = parents if forward else children

            def score(node):
                values = [positions[p] for p in neighbours[node]]
                return sum(values) / len(values) if values else positions[node]

            layers[index].sort(key=lambda node: (score(node), positions[node], order[node]))
            positions.update({node: i for i, node in enumerate(layers[index])})
    return layers


def _layout(sizes, edges, order):
    """Arrange independent chains separately; all rectangles have generous gutters."""
    adjacent = defaultdict(set)
    for a, b in edges:
        adjacent[a].add(b)
        adjacent[b].add(a)
    todo = set(sizes)
    groups = []
    while todo:
        root = min(todo, key=order.get)
        group, stack = set(), [root]
        while stack:
            node = stack.pop()
            if node not in todo:
                continue
            todo.remove(node)
            group.add(node)
            stack.extend(adjacent[node])
        groups.append(group)
    positions, y_base, total_width = {}, 0.0, 0.0
    for group in groups:
        layers = _layers(group, [(a, b) for a, b in edges if a in group and b in group], order)
        heights = [sum(sizes[n][1] for n in col) + 44 * (len(col) - 1) for col in layers]
        height = max(heights, default=0)
        x = 0.0
        for layer, col_height in zip(layers, heights):
            width = max(sizes[n][0] for n in layer)
            y = y_base + (height - col_height) / 2
            for node in layer:
                positions[node] = (x + (width - sizes[node][0]) / 2, y)
                y += sizes[node][1] + 44
            x += width + 112
        total_width = max(total_width, x - 112)
        y_base += height + 80
    return positions, total_width, max(0, y_base - 80)


def clean_document(document: FormulaDocument) -> None:
    """Change only presentation: IDs, links, port bindings and values stay intact."""
    blocks, frames, comments = document.blocks, document.frames, document.comments
    links = [(a, b) for a, b, _ in connections(blocks)]
    order = {bid: (float(b.get("y", 0)), float(b.get("x", 0)), bid) for bid, b in blocks.items()}

    # Frames are indivisible outer nodes. Shared members (legacy documents) remain
    # one cluster, rather than duplicating a block or silently changing membership.
    clusters = []
    for fid, frame in sorted(frames.items()):
        members = set(frame.get("members", [])) & blocks.keys()
        cluster = {"frames": [fid], "members": members}
        for existing in list(clusters):
            if cluster["members"] & existing["members"]:
                cluster["members"] |= existing["members"]
                cluster["frames"] += existing["frames"]
                clusters.remove(existing)
        clusters.append(cluster)
    owners, units = {}, {}
    for cluster in clusters:
        key = ("frame", min(cluster["frames"]))
        members = cluster["members"]
        inner, width, height = _layout(
            {bid: block_size(blocks[bid]) for bid in members},
            [(a, b) for a, b in links if a in members and b in members],
            order,
        )
        ports = max(
            (
                max(1, len(frames[f].get("inputs", [])), len(frames[f].get("outputs", [])))
                for f in cluster["frames"]
            ),
            default=1,
        )
        collapsed = len(cluster["frames"]) == 1 and frames[cluster["frames"][0]].get("collapsed")
        units[key] = {
            **cluster,
            "inner": inner,
            "size": (164.0, max(52.0, 28 + ports * 21))
            if collapsed
            else (max(170, width + 192), max(height + 60, 54 + ports * 21)),
        }
        owners.update({bid: key for bid in members})
    for bid, block in blocks.items():
        if bid not in owners:
            key = ("block", bid)
            owners[bid] = key
            units[key] = {"members": {bid}, "size": block_size(block), "frames": []}

    # Associate notes with their nearest existing node before moving anything.
    notes = defaultdict(list)
    for cid, comment in sorted(comments.items()):
        x, y = float(comment["x"]) + 85, float(comment["y"]) + 25

        def distance(bid):
            block = blocks[bid]
            w, h = block_size(block)
            dx = max(float(block["x"]) - x, 0, x - float(block["x"]) - w)
            dy = max(float(block["y"]) - y, 0, y - float(block["y"]) - h)
            return dx * dx + dy * dy, bid

        if blocks:
            notes[owners[min(blocks, key=distance)]].append(cid)
        else:
            key = ("comment", cid)
            units[key] = {"members": set(), "size": (0, 0), "frames": []}
            notes[key].append(cid)
    sizes = {}
    unit_order = {}
    for key, unit in units.items():
        w, h = unit["size"]
        note_height = len(notes[key]) * 62 + (12 if notes[key] and h else 0)
        unit["note_height"] = note_height
        sizes[key] = (max(w, 170 if notes[key] else 0), h + note_height)
        unit_order[key] = min((order[bid] for bid in unit["members"]), default=(0, 0, key[1]))
    outer_links = set((owners[a], owners[b]) for a, b in links if owners[a] != owners[b])
    positions, _, _ = _layout(sizes, outer_links, unit_order)
    for key, unit in units.items():
        x, y = positions[key]
        x, y = x + 64, y + 64
        for index, cid in enumerate(notes[key]):
            comments[cid].update(x=x + (sizes[key][0] - 170) / 2, y=y + index * 62)
        x += (sizes[key][0] - unit["size"][0]) / 2
        y += unit["note_height"]
        if unit["frames"]:
            for bid, (ix, iy) in unit["inner"].items():
                blocks[bid].update(x=x + 96 + ix, y=y + 34 + iy)
            for fid in unit["frames"]:
                frames[fid].update(x=x, y=y)
        else:
            for bid in unit["members"]:
                blocks[bid].update(x=x, y=y)
    document.wire_routes.clear()
    document.auto_route = True


def segments(points):
    return zip(points[::2], points[1::2], points[2::2], points[3::2])


def segment_hits_rect(a: Point, b: Point, rect: Rect) -> bool:
    """Strict interior collision; following a clearance boundary is allowed."""
    left, top, right, bottom = rect
    if a[1] == b[1]:
        return top < a[1] < bottom and max(a[0], b[0]) > left and min(a[0], b[0]) < right
    return left < a[0] < right and max(a[1], b[1]) > top and min(a[1], b[1]) < bottom


def _simplify(points: list[Point]) -> list[float]:
    result = []
    for point in points:
        if result and point == result[-1]:
            continue
        if len(result) > 1 and (
            result[-2][0] == result[-1][0] == point[0] or result[-2][1] == result[-1][1] == point[1]
        ):
            # Do not erase a reversal through a port/obstacle.
            a, b = result[-2], result[-1]
            if (b[0] - a[0]) * (point[0] - b[0]) >= 0 and (b[1] - a[1]) * (point[1] - b[1]) >= 0:
                result.pop()
        result.append(point)
    if len(result) == 1:
        result.append(result[0])
    return [coordinate for point in result for coordinate in point]


def _wire_penalty(a, b, occupied):
    cost = 0.0
    horizontal = a[1] == b[1]
    for x1, y1, x2, y2 in occupied:
        if horizontal and y1 == y2 == a[1]:
            cost += 2 * max(
                0, min(max(a[0], b[0]), max(x1, x2)) - max(min(a[0], b[0]), min(x1, x2))
            )
        elif not horizontal and x1 == x2 == a[0]:
            cost += 2 * max(
                0, min(max(a[1], b[1]), max(y1, y2)) - max(min(a[1], b[1]), min(y1, y2))
            )
        elif horizontal and x1 == x2:
            cost += (
                36
                if min(a[0], b[0]) < x1 < max(a[0], b[0]) and min(y1, y2) < a[1] < max(y1, y2)
                else 0
            )
        elif not horizontal and y1 == y2:
            cost += (
                36
                if min(a[1], b[1]) < y1 < max(a[1], b[1]) and min(x1, x2) < a[0] < max(x1, x2)
                else 0
            )
    return cost


def route_orthogonal(start: Point, end: Point, obstacles: list[Rect], occupied=()) -> list[float]:
    """Right-to-left ports, clearance from nodes, few bends and separated lanes.

    Try simple channel routes first, then a rectilinear visibility-grid search.
    Occupied wire segments are a soft cost: crossings remain possible in a dense
    graph, but blocks are hard obstacles. Endpoint stubs are exempt from collision
    with their own block only, not unrelated elements.
    """
    clearance, escape = 10.0, 22.0
    boxes = [
        (l - clearance, t - clearance, r + clearance, b + clearance) for l, t, r, b in obstacles
    ]
    source, target = (start[0] + escape, start[1]), (end[0] - escape, end[1])

    def clear(a, b):
        return not any(segment_hits_rect(a, b, box) for box in boxes)

    def score(path):
        points = _simplify([start, *path, end])
        # Only central segments require clearance; ports pierce their own body.
        if any(not clear(a, b) for a, b in zip(path, path[1:])):
            return math.inf
        return sum(
            abs(x2 - x1) + abs(y2 - y1) + _wire_penalty((x1, y1), (x2, y2), occupied)
            for x1, y1, x2, y2 in segments(points)
        ) + 22 * (len(points) // 2 - 2)

    candidates = []
    low, high = sorted((source[0], target[0]))
    channels = {(source[0] + target[0]) / 2, low, high}
    channels.update(low + fraction * (high - low) for fraction in (0.2, 0.35, 0.65, 0.8))
    channels.update(x for box in boxes for x in (box[0], box[2]) if low - 100 <= x <= high + 100)
    for x in sorted(channels):
        candidates.append([source, (x, source[1]), (x, target[1]), target])
    rows = {source[1] - 24, source[1] + 24, target[1] - 24, target[1] + 24}
    rows.update(y for box in boxes for y in (box[1], box[3]))
    for y in sorted(rows):
        candidates.append([source, (source[0], y), (target[0], y), target])
    middle = (source[0] + target[0]) / 2
    best = min(candidates, key=lambda p: (score(p), abs(p[1][0] - middle), tuple(p)))
    if math.isfinite(score(best)):
        return _simplify([start, *best, end])

    # Hanan grid: every obstacle edge is a possible corridor. No fixed pixel grid
    # or diagonal movement; narrow gaps work at any canvas zoom.
    xs = sorted({source[0], target[0], *[x for box in boxes for x in (box[0], box[2])]})
    ys = sorted({source[1], target[1], *[y for box in boxes for y in (box[1], box[3])]})
    xs = [min(xs) - 24, *xs, max(xs) + 24]
    ys = [min(ys) - 24, *ys, max(ys) + 24]
    origin = (xs.index(source[0]), ys.index(source[1]), 0)
    goal = (xs.index(target[0]), ys.index(target[1]))
    queue, costs, previous = [(0.0, origin)], {origin: 0.0}, {}
    final = None
    edge_clearance = {}
    while queue:
        estimate, state = heapq.heappop(queue)
        ix, iy, direction = state
        a = (xs[ix], ys[iy])
        if estimate > costs[state] + abs(a[0] - target[0]) + abs(a[1] - target[1]) + 1e-8:
            continue
        if (ix, iy) == goal:
            final = state
            break
        for nx, ny, axis in ((ix - 1, iy, 1), (ix + 1, iy, 1), (ix, iy - 1, 2), (ix, iy + 1, 2)):
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            b = (xs[nx], ys[ny])
            edge = tuple(sorted((a, b)))
            if edge not in edge_clearance:
                edge_clearance[edge] = clear(a, b)
            if not edge_clearance[edge]:
                continue
            cost = costs[state] + abs(a[0] - b[0]) + abs(a[1] - b[1])
            cost += 22 if direction and direction != axis else 0
            cost += _wire_penalty(a, b, occupied)
            next_state = (nx, ny, axis)
            if cost < costs.get(next_state, math.inf):
                costs[next_state] = cost
                previous[next_state] = state
                heapq.heappush(
                    queue, (cost + abs(b[0] - target[0]) + abs(b[1] - target[1]), next_state)
                )
    if final is None:
        # A user can manually drag an unrelated block directly on a port. Keep
        # the connection visible instead of hanging or discarding its data.
        return _simplify([start, source, (source[0], target[1]), target, end])
    path = []
    while final is not None:
        path.append((xs[final[0]], ys[final[1]]))
        final = previous.get(final)
    return _simplify([start, *reversed(path), end])


def wire_midpoint(points: list[float]) -> Point:
    """Place the draggable handle on the wire, not in the middle of empty space."""
    length = sum(abs(x2 - x1) + abs(y2 - y1) for x1, y1, x2, y2 in segments(points))
    remaining = length / 2
    for x1, y1, x2, y2 in segments(points):
        span = abs(x2 - x1) + abs(y2 - y1)
        if remaining <= span and span:
            ratio = remaining / span
            return x1 + ratio * (x2 - x1), y1 + ratio * (y2 - y1)
        remaining -= span
    return points[0], points[1]
