"""几何解析模块。

负责从 GeoJSON 中解析配送范围多边形，支持：
  - Polygon（外环 + 带洞内环）
  - MultiPolygon
  - 空几何 / 无效几何识别
  - 跨经线（±180°）多边形：通过"解包"（unwrap）得到连续经度坐标，
    为后续判定内核与网格索引提供统一、正确的几何表示。

坐标约定：GeoJSON 坐标顺序为 [经度 x, 纬度 y]。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Sequence

# 坐标点：(经度 x, 纬度 y)
Point = Tuple[float, float]
Ring = List[Point]


@dataclass
class Polygon:
    """一个"实心"多边形 = 单个外环 + 若干洞内环。"""

    exterior: Ring
    holes: List[Ring] = field(default_factory=list)

    # 解包后的连续外环（跨经线时经度被拉平，保证判定正确）
    exterior_unwrapped: Optional[Ring] = field(default=None, repr=False)

    def fill(self) -> Sequence[Ring]:
        return [self.exterior, *self.holes]

    def unwrapped_fill(self) -> Sequence[Ring]:
        rings = [self.exterior_unwrapped or self.exterior]
        rings.extend(_unwrap_ring(h) for h in self.holes)
        return rings


@dataclass
class Region:
    """一个可命中的配送区域，可能由多个 Polygon 组成（几何并集）。"""

    id: str
    name: str
    polygons: List[Polygon] = field(default_factory=list)

    # 空/无效几何标记与告警信息
    empty: bool = False
    warning: Optional[str] = None


def _unwrap_ring(ring: Ring) -> Ring:
    """将闭环的经度解包为连续值。

    相邻顶点经度差 > 180° 视为跨经线跳变，对该点之后的坐标累加 ±360°
    偏移，使整圈经度连续单调。非跨经线的环保持原样。
    """
    n = len(ring)
    if n == 0:
        return []
    out: Ring = []
    offset = 0.0
    px, py = ring[0]
    out.append((px, py))
    for i in range(1, n):
        x, y = ring[i]
        d = x - px
        if d > 180.0:
            offset -= 360.0
        elif d < -180.0:
            offset += 360.0
        out.append((x + offset, y))
        px, py = x, y
    return out


def _continuous_lon_span(rings: Sequence[Ring]) -> Tuple[float, float]:
    """返回一组环解包后的经度连续区间 [min, max]。"""
    lo, hi = math.inf, -math.inf
    for r in rings:
        u = _unwrap_ring(r)
        for x, _ in u:
            if x < lo:
                lo = x
            if x > hi:
                hi = x
    # 防御：合法闭环经度较差 < 360°
    if lo == math.inf:
        return (0.0, 0.0)
    return (lo, hi)


def _parse_linear_ring(coords: Any, idx: int) -> Optional[Ring]:
    """把一条 linear-ring 坐标解析为闭环点列表。不合法返回 None。"""
    if not isinstance(coords, list) or not coords:
        return None
    ring: Ring = []
    for p in coords:
        if (
            isinstance(p, (list, tuple))
            and len(p) >= 2
            and isinstance(p[0], (int, float))
            and isinstance(p[1], (int, float))
        ):
            ring.append((float(p[0]), float(p[1])))
        else:
            return None
    # 至少 3 个不同顶点且首尾闭合
    if len(_dedup(ring)) < 3 or ring[0] != ring[-1]:
        return None
    return ring


def _dedup(ring: Ring) -> List[Point]:
    out: List[Point] = []
    for p in ring:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _region_id(feature: Dict[str, Any], fallback: int) -> str:
    props = feature.get("properties") or {}
    for key in ("id", "name", "region_id", "region", "label"):
        v = props.get(key)
        if v is not None:
            return str(v)
    return f"region_{fallback}"


def _region_name(feature: Dict[str, Any], rid: str) -> str:
    props = feature.get("properties") or {}
    for key in ("name", "label", "title"):
        v = props.get(key)
        if v is not None:
            return str(v)
    return rid


def _parse_geometry(geom: Any, region: Region, warnings: List[str], gi: int):
    gtype = None
    if isinstance(geom, dict):
        gtype = geom.get("type")

    if gtype in (None, "GeometryCollection"):
        region.empty = True
        region.warning = "空几何 / 未支持类型，区域不参与命中判定"
        warnings.append(f"[{region.id}] {region.warning}")
        return

    if gtype == "Polygon":
        _polygons = [geom.get("coordinates") or []]
    elif gtype == "MultiPolygon":
        _polygons = geom.get("coordinates") or []
    else:
        region.empty = True
        region.warning = f"不支持的 geometry 类型: {gtype}，已跳过"
        warnings.append(f"[{region.id}] {region.warning}")
        return

    for poly_coords in _polygons:
        if not isinstance(poly_coords, list) or not poly_coords:
            continue
        exterior = _parse_linear_ring(poly_coords[0], 0)
        if exterior is None:
            region.empty = True
            region.warning = "外环无效，区域不参与命中判定"
            warnings.append(f"[{region.id}] {region.warning}")
            return
        poly = Polygon(exterior=exterior)
        # 其余环为洞
        for h in poly_coords[1:]:
            hole = _parse_linear_ring(h, 0)
            if hole is not None:
                poly.holes.append(hole)
        poly.exterior_unwrapped = _unwrap_ring(exterior)
        region.polygons.append(poly)

    if not region.polygons:
        region.empty = True
        region.warning = "多边形为空，区域不参与命中判定"
        warnings.append(f"[{region.id}] {region.warning}")


def parse_feature_collection(
    geojson: Any,
) -> Tuple[List[Region], List[str]]:
    """解析 GeoJSON（FeatureCollection），返回 (区域列表, 解析告警)。"""
    warnings: List[str] = []
    regions: List[Region] = []

    if not isinstance(geojson, dict):
        raise ValueError("输入必须是 GeoJSON 对象")

    fc_type = geojson.get("type")
    features: List[Any]
    if fc_type == "FeatureCollection":
        features = geojson.get("features") or []
    elif fc_type == "Feature":
        features = [geojson]
    else:
        raise ValueError(f"仅支持 FeatureCollection/Feature，收到 {fc_type}")

    for i, feature in enumerate(features):
        geom = feature.get("geometry") if isinstance(feature, dict) else None
        rid = _region_id(feature, i)
        region = Region(id=rid, name=_region_name(feature, rid))

        # 明确为空的几何
        if geom is None or (isinstance(geom, dict) and geom.get("coordinates") in (None, [])):
            region.empty = True
            region.warning = "geometry 为 null 或空，不参与命中判定"
            warnings.append(f"[{region.id}] {region.warning}")
        else:
            before = len(region.polygons)
            _parse_geometry(geom, region, warnings, i)
            if region.polygons:
                region.empty = False

        regions.append(region)

    return regions, warnings