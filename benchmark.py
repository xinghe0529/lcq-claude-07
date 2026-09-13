#!/usr/bin/env python3
"""批量判定 vs 逐条（全量遍历）判定 的耗时对比。

结果输出:
  - 总耗时 / 单点均耗时（秒、微秒/点）
  - 加速比（逐条 / 批量）

用法: python benchmark.py
"""
import random
import time

from geom import build_engine


def synthetic_geojson(n_anti=5, col=12, row=6):
    """生成 col×row 个矩形配送区 + 少量跨经线多边形，及对应测试点。"""
    features = []
    lon_step = 360.0 / col
    lat_step = 180.0 / row

    idx = [0]

    def add(geometry):
        i = idx[0]
        idx[0] += 1
        features.append(
            {
                "type": "Feature",
                "properties": {"id": f"r{i}"},
                "geometry": geometry,
            }
        )

    for c in range(col):
        for r in range(row):
            x = -180 + c * lon_step
            y = -90 + r * lat_step
            inner = lon_step * 0.8
            inner_lat = lat_step * 0.8
            add(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[x, y], [x + inner, y], [x + inner, y + inner_lat], [x, y + inner_lat], [x, y]]
                    ],
                }
            )

    # 跨越反子午线的窄带
    for k in range(n_anti):
        lat0 = -30 + k * 18
        add(
            {
                "type": "Polygon",
                "coordinates": [
                    [[178 + k * 0.3, lat0], [-179 + k * 0.3, lat0],
                     [-179 + k * 0.3, lat0 + 6], [178 + k * 0.3, lat0 + 6], [178 + k * 0.3, lat0]]
                ],
            }
        )

    return {"type": "FeatureCollection", "features": features}


def sample_points(n, col, row):
    lon_step = 360.0 / col
    lat_step = 180.0 / row
    pts = []
    for _ in range(n):
        c = random.randint(0, col - 1)
        r = random.randint(0, row - 1)
        x = -180 + c * lon_step + random.uniform(0, lon_step * 0.5)
        y = -90 + r * lat_step + random.uniform(0, lat_step * 0.5)
        pts.append((x, y))
    return pts


def evaluate_naive(x, y, regions):
    from geom.kernel import hit_region
    for region in regions.values():
        if hit_region(x, y, region, 1e-7, "include"):
            return True
    return False


def main():
    COL, ROW = 12, 6
    N_POINTS = 2000

    print("构建测试数据 ...")
    fc = synthetic_geojson(col=COL, row=ROW)
    index, regions, warnings = build_engine(fc, n_col=36, n_row=18)
    print(f"区域数: {len(regions)}（告警 {len(warnings)}）")

    pts = sample_points(N_POINTS, COL, ROW)
    pts += [(180.0, -9.0), (179.5, -25.0), (-179.5, -9.0)]  # 跨经线命中点

    # --- 网格索引批量 ---
    t0 = time.perf_counter()
    all_res = []
    for x, y in pts:
        all_res.append(_evaluate_indexed(x, y, index, regions))
    build_ms = 0.0
    t_grid = time.perf_counter() - t0

    # --- 逐条全量遍历 ---
    t1 = time.perf_counter()
    for x, y in pts:
        evaluate_naive(x, y, regions)
    t_naive = time.perf_counter() - t1

    n = len(pts)
    speedup = t_naive / t_grid if t_grid > 0 else float("inf")

    def fmt(sec):
        return f"{sec*1e6:.1f} µs/点"

    line = "=" * 60
    print(line)
    print(f"测试点数: {n}")
    print(f"网格索引批量: 总 {t_grid:.4f} s  | {fmt(t_grid/n)}")
    print(f"逐条全量遍历: 总 {t_naive:.4f} s | {fmt(t_naive/n)}")
    print(line)
    print(f"加速比（逐条/批量）≈ {speedup:.2f}x")
    print(line)
    # 冒烟：跨经线命中统计
    anti_hits = [r["region"] is not None for r in all_res[-3:]]
    print(f"跨经线样例 (180,-9)/(-179.5,-9)/(179.5,-25) 命中: {anti_hits}")
    return speedup


def _evaluate_indexed(x, y, index, regions):
    from geom.kernel import evaluate_point
    return evaluate_point(x, y, index, regions)


if __name__ == "__main__":
    main()