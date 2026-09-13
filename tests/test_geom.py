# -*- coding: utf-8 -*-
"""测试：覆盖 点在边界上 / 点在洞里 / 跨经线 / 批量混合命中  四类情况。"""
import pytest
from fastapi.testclient import TestClient

from geom.index import GridIndex
from geom.kernel import evaluate_point
from geom.parse import parse_feature_collection


def make_index(fc):
    regions, _ = parse_feature_collection(fc)
    index = GridIndex(n_col=36, n_row=18)
    index.build(regions)
    return index, {r.id: r for r in regions}


# ---------- 1) 点在外环边界上 ----------
def test_point_on_boundary():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "A"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                },
            }
        ],
    }
    index, regions = make_index(fc)
    res = evaluate_point(0.0, 5.0, index, regions, tol=1e-7, boundary_rule="include")
    assert res["relation"] == "boundary"
    assert res["region"]["id"] == "A"

    # boundary 规则为 exclude 时应判为 outside
    res2 = evaluate_point(0.0, 5.0, index, regions, tol=1e-7, boundary_rule="exclude")
    assert res2["relation"] == "outside"
    assert res2["region"] is None


# ---------- 2) 点在洞里 ----------
def test_point_in_hole():
    body = [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]
    hole = [[5, 5], [15, 5], [15, 15], [5, 15], [5, 5]]
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "H"},
                "geometry": {"type": "Polygon", "coordinates": [body, hole]},
            }
        ],
    }
    index, regions = make_index(fc)
    assert evaluate_point(10, 10, index, regions)["relation"] == "outside"  # 洞内
    assert evaluate_point(2, 2, index, regions)["relation"] == "inside"  # 洞外、外环内
    # 洞的围环上也算 boundary
    assert evaluate_point(5, 10, index, regions, tol=1e-7)["relation"] == "boundary"


# ---------- 3) 跨经线（反子午线）多边形 ----------
def test_antimeridian():
    # 从北纬的一侧跨越 ±180° 的窄带矩形：经度 179 ~ -179（等价 179~181）
    coords = [[[179, 0], [-179, 0], [-179, 10], [179, 10], [179, 0]]]
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "ANTI"},
                "geometry": {"type": "Polygon", "coordinates": coords},
            }
        ],
    }
    index, regions = make_index(fc)

    # 经度 180 的正反两侧都应命中
    assert evaluate_point(180, 5, index, regions)["relation"] == "inside"
    assert evaluate_point(179.5, 5, index, regions)["relation"] == "inside"
    assert evaluate_point(-179.5, 5, index, regions)["relation"] == "inside"
    # 0° 这类远处点不应命中
    assert evaluate_point(0, 5, index, regions)["relation"] == "outside"
    # 网格查询也应在 ±180 附近取到该区域
    assert "ANTI" in index.query(180, 5)
    assert "ANTI" in index.query(-179.5, 5)


# ---------- 坐标精度误差 / 容差 ----------
def test_precision_tolerance():
    # 点在围环外 tol 内，质量较差坐标，应判为 boundary
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "P"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                },
            }
        ],
    }
    index, regions = make_index(fc)
    # 点略偏出左边界 0.0001°，用 1e-3 容差吸收后应为 boundary
    res = evaluate_point(-0.0001, 5, index, regions, tol=1e-3)
    assert res["relation"] == "boundary"
    # 用严格容差则 outside
    res2 = evaluate_point(-0.0001, 5, index, regions, tol=1e-9)
    assert res2["relation"] == "outside"


# ---------- 空几何 ----------
def test_empty_geometry():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "E"}, "geometry": None},
            {
                "type": "Feature",
                "properties": {"id": "EMPTY"},
                "geometry": {"type": "Polygon", "coordinates": []},
            },
        ],
    }
    regions, warnings = parse_feature_collection(fc)
    assert all(r.empty for r in regions)
    assert len(warnings) == 2
    index = GridIndex()
    index.build(regions)
    # 任何点都不会命中空区域
    res = evaluate_point(5, 5, index, {r.id: r for r in regions})
    assert res["relation"] == "outside"


# ---------- 4) 批量混合命中 ----------
def test_batch_mixed_hits():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "A"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "B"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "NO"},
                "geometry": None,
            },
        ],
    }
    index, regions = make_index(fc)
    points = [[5, 5], [25, 5], [50, 50], [0, 5]]  # in A, in B, outside, boundary of A
    results = [evaluate_point(x, y, index, regions) for x, y in points]
    rels = [r["relation"] for r in results]
    assert rels[:2] == ["inside", "inside"]
    assert rels[2] == "outside"
    assert rels[3] == "boundary"
    assert results[0]["region"]["id"] == "A"
    assert results[1]["region"]["id"] == "B"


# ---------- 结合 FastAPI 接口的批量耗时对比 ----------
def test_api_batch_and_timing():
    from main import app

    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": str(i)},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[i * 10, 0], [i * 10 + 8, 0], [i * 10 + 8, 8], [i * 10, 8], [i * 10, 0]]],
                },
            }
            for i in range(20)
        ],
    }
    client = TestClient(app)
    r = client.post("/regions", json={"geojson": fc})
    assert r.status_code == 200
    assert r.json()["regions"] == 20

    pts = [[i % 200 - 100, (i * 7) % 60 - 30] for i in range(50)]
    r2 = client.post("/batch", json={"points": pts, "compare_sequential": True})
    assert r2.status_code == 200
    body = r2.json()
    assert body["count"] == 50
    assert body["timing"]["speedup_vs_sequential"] is not None
    assert body["timing"]["speedup_vs_sequential"] >= 1.0