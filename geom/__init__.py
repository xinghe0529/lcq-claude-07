"""配送区域点归属判定引擎。

模块划分：
  - geom.parse   几何解析（带洞 / MultiPolygon / 空几何 / 跨经线解包）
  - geom.index   网格索引（循环经度分桶）
  - geom.kernel  判定内核（射线法 + 边界归属 + 容差）
  - geom.api     FastAPI 接口（单点 / 批量 / 加载）
"""

from .parse import Region, Polygon, parse_feature_collection
from .index import GridIndex
from .kernel import evaluate_point

__all__ = [
    "Region",
    "Polygon",
    "parse_feature_collection",
    "GridIndex",
    "evaluate_point",
]


def build_engine(geojson, n_col=36, n_row=18):
    """便捷入口：解析 + 建索引，返回 (index, regions, warnings)。"""
    regions, warnings = parse_feature_collection(geojson)
    index = GridIndex(n_col=n_col, n_row=n_row)
    index.build(regions)
    return index, {r.id: r for r in regions}, warnings