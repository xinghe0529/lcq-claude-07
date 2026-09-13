"""配送范围点归属判定服务入口。

启动: uvicorn main:app --reload
文档: http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI

from geom.api import router

app = FastAPI(
    title="配送范围点归属判定服务",
    description="基于 GeoJSON 多边形（含带洞、跨经线）+ 网格索引的单点/批量点归属判定",
    version="1.0.0",
)
app.include_router(router, tags=["geofence"])


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)