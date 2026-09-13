# 配送范围点归属判定服务

基于 GeoJSON 多边形（含带洞、MultiPolygon、跨 ±180° 经线）与等经纬网格索引的
单点 / 批量点归属判定服务。

## 模块划分

- `geom/parse.py`  几何解析（带洞 / MultiPolygon / 空几何 / 跨经线解包）
- `geom/index.py`  网格索引（循环经度分桶）
- `geom/kernel.py` 判定内核（射线法 + 边界归属 + 容差）
- `geom/api.py`    FastAPI 接口（加载区域 / 单点判定 / 批量判定）
- `main.py`        ASGI 入口
- `benchmark.py`   批量判定耗时基准

## 运行

```bash
pip install -r requirements.txt
uvicorn main:app --reload          # 接口文档 http://127.0.0.1:8000/docs
```

## 测试

```bash
python3 -m pytest -q
python3 benchmark.py
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/health`  | 健康检查 |
| POST | `/regions` | 加载 GeoJSON（FeatureCollection / Feature）并构建网格索引 |
| POST | `/point`   | 单点判定，返回 inside / boundary / outside |
| POST | `/batch`   | 批量判定，附批量与逐条耗时对比 |
