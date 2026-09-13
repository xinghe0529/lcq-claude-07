"""判定内核。

提供点与多边形的空间关系判定，以及"命中"装配逻辑。要点：
  - 基于"解包"连续坐标的射线法（even-odd）判定点在环内；
  - 对落在围环（边界）上、洞内、跨经线、距离围环在容差内的点给出明确关系；
  - 坐标精度误差用 tol（经纬度度数）阈值处理：点到围环直线距离 <= tol 判为"边界"。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .parse import Polygon, Region
from .index import GridIndex

# 关系枚举
INSIDE = "inside"
BOUNDARY = "boundary"
OUTSIDE = "outside"

Relation = str  # one of inside/boundary/outside


def _sq_seg_distance(x: float, y: float, a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """点到线段的最短距离平方（在经度/纬度平面内，适用于小范围容差计量）。"""
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return (x - ax) ** 2 + (y - ay) ** 2
    t = ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx = ax + t * dx
    cy = ay + t * dy
    return (x - cx) ** 2 + (y - cy) ** 2


def relation_to_ring(x: float, y: float, ring: List[Tuple[float, float]], tol: float) -> Optional[Relation]:
    """返回点相对给定（已解包）环的关系：inside / boundary / 不在环内(None)。

    只接受位于环经度跨区附近的候选点（处理跨经线需把点的经度折回环所在区间）。
    """
    n = len(ring)
    if n < 3:
        return None

    xmin, xmax = ring[0][0], ring[0][0]
    for px, _ in ring[1:]:
        if px < xmin:
            xmin = px
        if px > xmax:
            xmax = px

    # 环形经度较差恒 < 360°，点折回环区间最多一个候选成立
    xloop = [x, x + 360.0, x - 360.0, x + 720.0, x - 720.0]
    cand = [cx for cx in xloop if xmin - tol <= cx <= xmax + tol]
    if not cand:
        return None
    cx = cand[0]

    # 边界（含容差）
    tol2 = tol * tol
    for i in range(n - 1):
        if _sq_seg_distance(cx, y, ring[i], ring[i + 1]) <= tol2:
            return BOUNDARY

    # even-odd 射线法（向右水平射线）
    inside = False
    for i in range(n - 1):
        xi, yi = ring[i]
        xj, yj = ring[i + 1]
        if (yi > y) != (yj > y):
            xint = xi + (y - yi) * (xj - xi) / (yj - yi)
            if xint > cx:
                inside = not inside
    return INSIDE if inside else None


def polygon_relation(x: float, y: float, poly: Polygon, tol: float) -> Relation:
    """点相对一个实心多边形（外环-洞）的关系。

    具体规则：
      - 在外环上 -> boundary
      - 在外环内且不在任何洞内 -> inside
      - 在外环内但位于洞内 -> outside
      - 在洞的围环上 -> boundary
    """
    rings = poly.unwrapped_fill()
    er = relation_to_ring(x, y, rings[0], tol)
    if er is None:
        return OUTSIDE
    if er == BOUNDARY:
        return BOUNDARY
    # er == inside
    for hole in rings[1:]:
        hr = relation_to_ring(x, y, hole, tol)
        if hr == INSIDE:
            return OUTSIDE
        if hr == BOUNDARY:
            return BOUNDARY
    return INSIDE


def region_relation(x: float, y: float, region: Region, tol: float) -> Relation:
    """点相对一个区域（多边形并集）的关系，取最强关系。"""
    if region.empty or not region.polygons:
        return OUTSIDE
    rel = OUTSIDE
    for poly in region.polygons:
        r = polygon_relation(x, y, poly, tol)
        if r == INSIDE:
            return INSIDE
        if r == BOUNDARY:
            rel = BOUNDARY
    return rel


def _apply_boundary_rule(rel: Relation, rule: str) -> Relation:
    """把 boundary 关系按规则归并：include 保留 boundary，exclude 视为 outside。"""
    if rel == BOUNDARY and rule == "exclude":
        return OUTSIDE
    return rel


def hit_region(x: float, y: float, region: Region, tol: float, boundary_rule: str) -> Optional[Relation]:
    """返回命中关系，若区域无关（outside）则返回 None。"""
    rel = region_relation(x, y, region, tol)
    rel = _apply_boundary_rule(rel, boundary_rule)
    return rel if rel != OUTSIDE else None


def evaluate_point(
    x: float,
    y: float,
    index: GridIndex,
    regions: Dict[str, Region],
    tol: float = 1e-7,
    boundary_rule: str = "include",
) -> Dict:
    """单点判定：用网格索引缩小候选集，装配主命中 + 全部接触区域。

    返回结构：
      {
        "x": 经度, "y": 纬度,
        "relation": inside/boundary/outside,   // 主关系（已应用边界规则）
        "region": {id,name} 或 null,            // 命中的主要区域
        "boundary_rule": ...,
        "hits": [ {region_id,name,relation} ],  // 所有接触区域（含边界）
        "candidates_checked": int,              // 实际核验的候选数
      }
    """
    candidates = index.query(x, y)
    touched: List[Dict] = []
    inside_region: Optional[Region] = None
    boundary_region: Optional[Region] = None

    for rid in candidates:
        region = regions.get(rid)
        if region is None or region.empty:
            continue
        rel = region_relation(x, y, region, tol)
        if rel == BOUNDARY:
            touched.append({"region_id": rid, "name": region.name, "relation": BOUNDARY})
            if boundary_region is None:
                boundary_region = region
        elif rel == INSIDE:
            touched.append({"region_id": rid, "name": region.name, "relation": INSIDE})
            if inside_region is None:
                inside_region = region

    if inside_region is not None:
        relation, region_hit = INSIDE, inside_region
    elif boundary_region is not None and boundary_rule == "include":
        relation, region_hit = BOUNDARY, boundary_region
    else:
        relation, region_hit = OUTSIDE, None

    return {
        "x": x,
        "y": y,
        "relation": relation,
        "region": {"id": region_hit.id, "name": region_hit.name} if region_hit else None,
        "boundary_rule": boundary_rule,
        "hits": touched,
        "candidates_checked": len(candidates),
    }