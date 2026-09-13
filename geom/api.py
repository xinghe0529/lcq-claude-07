"""接口层（FastAPI）。

提供单点判定 / 批量判定 / 加载区域三个端点，并内置批量 vs 逐条耗时对比。
"""

from __future__ import annotations

import time
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .parse import Region, parse_feature_collection
from .index import GridIndex
from .kernel import evaluate_point, hit_region, region_relation

router = APIRouter()

# ---- 数据模型 ----
class LoadRequest(BaseModel):
    geojson: dict
    n_col: int = 36
    n_row: int = 18


class LoadResponse(BaseModel):
    regions: int
    empty_skipped: int
    warnings: List[str] = []


class PointRequest(BaseModel):
    lon: float
    lat: float
    tol: float = Field(default=1e-7, description="距离围环多少（经纬度度数）以内视为边界，用于吸收坐标精度误差")
    boundary_rule: Literal["include", "exclude"] = "include"


class BatchRequest(BaseModel):
    points: List[List[float]] = Field(min_length=1, description="[[lon,lat], ...]")
    tol: float = Field(default=1e-7)
    boundary_rule: Literal["include", "exclude"] = "include"
    compare_sequential: bool = True


class RegionHit(BaseModel):
    region_id: str
    name: str
    relation: str


class PointResponse(BaseModel):
    x: float
    y: float
    relation: str
    region: Optional[dict]
    boundary_rule: str
    hits: List[RegionHit] = []
    candidates_checked: int = 0


class BatchResponse(BaseModel):
    count: int
    results: List[PointResponse]
    timing: dict


# ---- 引擎状态（进程内单例）----
class _Engine:
    def __init__(self):
        self.regions: dict = {}      # id -> Region
        self.index: Optional[GridIndex] = None
        self.warnings: List[str] = []


_engine = _Engine()


def _require_index():
    if _engine.index is None:
        raise HTTPException(status_code=400, detail="尚未加载区域，请先 POST /regions")


# ---- 端点 ----
@router.post("/regions", response_model=LoadResponse)
def load_regions(req: LoadRequest) -> LoadResponse:
    """解析 GeoJSON 并构建网格索引。"""
    try:
        regions, warnings = parse_feature_collection(req.geojson)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    index = GridIndex(n_col=req.n_col, n_row=req.n_row)
    index.build(regions)

    _engine.regions = {r.id: r for r in regions}
    _engine.index = index
    _engine.warnings = warnings

    empty = sum(1 for r in regions if r.empty)
    return LoadResponse(regions=len(regions), empty_skipped=empty, warnings=warnings)


@router.post("/point", response_model=PointResponse)
def single_point(req: PointRequest) -> PointResponse:
    """单点判定。"""
    _require_index()
    res = evaluate_point(req.lon, req.lat, _engine.index, _engine.regions, req.tol, req.boundary_rule)
    return PointResponse(**res)


def _evaluate_naive(lon, lat, regions, tol, boundary_rule):
    """逐条（遍历全部区域）判定，用于耗时对比。"""
    rel = None
    region_hit = None
    for region in regions.values():
        r = hit_region(lon, lat, region, tol, boundary_rule)
        if r:
            rel = r
            region_hit = region
    return rel, region_hit


@router.post("/batch", response_model=BatchResponse)
def batch_points(req: BatchRequest) -> BatchResponse:
    """批量判定，附批量 vs 逐条耗时对比。"""
    _require_index()
    pts = [(p[0], p[1]) for p in req.points]

    # 1) 网格索引 + 批量
    t0 = time.perf_counter()
    results = [
        PointResponse(**evaluate_point(x, y, _engine.index, _engine.regions, req.tol, req.boundary_rule))
        for x, y in pts
    ]
    t_grid = time.perf_counter() - t0

    # 2) 逐条遍历全部区域（对照组）
    naive_sec = None
    speedup = None
    if req.compare_sequential:
        t1 = time.perf_counter()
        for x, y in pts:
            _evaluate_naive(x, y, _engine.regions, req.tol, req.boundary_rule)
        t_naive = time.perf_counter() - t1
        naive_sec = t_naive
        speedup = (t_naive / t_grid) if t_grid > 0 else None

    timing = {
        "batch_grid_sec": round(t_grid, 6),
        "sequential_naive_sec": round(naive_sec, 6) if naive_sec is not None else None,
        "speedup_vs_sequential": round(speedup, 2) if speedup else None,
        "points": len(pts),
    }
    return BatchResponse(count=len(pts), results=results, timing=timing)