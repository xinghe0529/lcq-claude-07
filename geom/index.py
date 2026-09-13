"""网格索引构建。

采用"等经纬网格 + 循环经度分桶"：
  - 世界被分成 n_col × n_row 个网格单元（经度 360°，纬度 180°）；
  - 经度按"循环"处理（index = lon mod 360），这样跨经线多边形在 -180/+180
    两侧都能被正确登记与查询；
  - 每个面若非空，用其解包后的连续经度区间 + 纬度区间，计算覆盖到的
    所有网格单元，并把 region_id 登记进每个单元；
  - 查询时只需在点所在单元内取候选，显著减少判定内核的调用次数。

索引构建主要耗时在初始化（建一次、多次查），判定阶段为 O(1) 取桶 + O(k) 核验。
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Set, Tuple

from .parse import Region, _continuous_lon_span, _unwrap_ring


class GridIndex:
    def __init__(self, n_col: int = 36, n_row: int = 18):
        if n_col <= 0 or n_row <= 0:
            raise ValueError("网格行列数必须为正整数")
        self.n_col = n_col
        self.n_row = n_row
        self.cell_w = 360.0 / n_col
        self.cell_h = 180.0 / n_row
        # col,row -> set[region_id]
        self._buckets: Dict[Tuple[int, int], Set[str]] = defaultdict(set)

    # ---- 定位 ----
    def _column(self, lon: float) -> int:
        c = int(lon % 360.0 / self.cell_w)
        return min(self.n_col - 1, max(0, c))

    def _row(self, lat: float) -> int:
        r = int((lat + 90.0) / self.cell_h)
        return min(self.n_row - 1, max(0, r))

    # ---- 构建 ----
    def build(self, regions: Iterable[Region]) -> int:
        self._buckets.clear()
        count = 0
        for region in regions:
            if region.empty or not region.polygons:
                continue
            lo, hi = _continuous_lon_span([p.exterior for p in region.polygons])
            lat_min, lat_max = self._lat_span(region)
            cols = self._cover_columns(lo, hi)
            for col in cols:
                for row in range(self.n_row):
                    # 行覆盖范围判定
                    if not self._row_in_range(row, lat_min, lat_max):
                        continue
                    self._buckets[(col, row)].add(region.id)
            count += 1
        return count

    def _lat_span(self, region: Region) -> Tuple[float, float]:
        lo, hi = math.inf, -math.inf
        for poly in region.polygons:
            for x, y in poly.exterior:
                if y < lo:
                    lo = y
                if y > hi:
                    hi = y
        if lo == math.inf:
            return (0.0, 0.0)
        return (lo, hi)

    def _row_in_range(self, row: int, lat_min: float, lat_max: float) -> bool:
        rlo = -90.0 + row * self.cell_h
        rhi = rlo + self.cell_h
        return lat_min < rhi and lat_max > rlo

    def _cover_columns(self, lo: float, hi: float) -> Set[int]:
        """返回连续经度区间 [lo,hi]（较差 < 360°）循环覆盖到的所有列。"""
        if hi <= lo:
            return {self._column(lo)}
        span = hi - lo
        steps = int(math.ceil(span / self.cell_w)) + 2
        cols: Set[int] = set()
        for k in range(steps):
            lon = lo + k * self.cell_w
            cols.add(self._column(lon))
        # 补上终点所在列
        cols.add(self._column(hi))
        return cols

    # ---- 查询 ----
    def query(self, lon: float, lat: float) -> List[str]:
        """取点所在网格单元及其 3×3 环形邻域的候选 region_id。

        经度列按循环处理，保证 -180/180 与 0/360 拼接处，以及点恰在单元分界线
        上的精度误差场景，都能覆盖到相邻单元，避免漏命。
        """
        c = self._column(lon)
        r = self._row(lat)
        found: Set[str] = set()
        for dc in (-1, 0, 1):
            cc = (c + dc) % self.n_col
            for dr in (-1, 0, 1):
                rr = max(0, min(self.n_row - 1, r + dr))
                found.update(self._buckets.get((cc, rr), ()))
        return list(found)