# -*- coding: utf-8 -*-
"""
Drawing2Model Agent — FastAPI 服务 (REST + Web UI)
- POST /api/jobs        提交重建任务 (multipart pdf 或模板)
- GET  /api/jobs/{id}   状态+历史
- GET  /api/jobs/{id}/artifacts/{kind}   下载 glb/stl/spec/report/render
- GET  /api/status      LLM 在线状态 + 案例库统计
- GET  /                Web UI (3DBuilderQwen)
运行: python server.py   (默认 8410)
MCP SSE 服务见 mcp_server.py (默认 8411)
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from llm.gateway import DEFAULT_BASE_URL
from app import jobs
import mcp_server  # MCP SSE 工具 (与 REST 共享 jobs 状态)
mcp = mcp_server.mcp

app = FastAPI(title="Drawing2Model Agent", version="0.2.0")


@app.get("/api/status")
async def status():
    from llm.gateway import get_gateway, _resolve_config
    from app import case_library
    gw = get_gateway()
    cfg = _resolve_config()
    masked = (cfg["api_key"][:4] + "***" + cfg["api_key"][-2:]) if len(cfg["api_key"]) >= 8 else "(empty)"
    return {"llm": gw.status(), "config": {
                "key_set": bool(cfg["api_key"]),
                "key_preview": masked,
                "base_url": cfg["base_url"],
                "vl_model": cfg["model_vl"],
                "text_model": cfg["model_text"],
                "env_hints": {
                    "LLM_API_KEY / DASHSCOPE_API_KEY": "API 凭证",
                    "LLM_BASE_URL": f"OpenAI 兼容 endpoint (默认: {DEFAULT_BASE_URL})",
                    "LLM_MODEL": "通用模型 (默认 qwen-vl-max)",
                    "LLM_VL_MODEL / LLM_TEXT_MODEL": "分别覆盖视觉/文本模型",
                }},
            "cases": len(case_library._load()),
            "jobs": jobs.list_jobs()}


@app.post("/api/jobs")
async def create_job(pdf: UploadFile | None = File(None),
                     use_template: bool = Form(False),
                     max_rounds: int = Form(4),
                     mode: str = Form("pipeline")):
    pdf_bytes = await pdf.read() if pdf is not None else None
    job_id = jobs.submit_job(pdf_bytes=pdf_bytes, use_template=use_template,
                             max_rounds=max_rounds,
                             mode="evolve" if mode == "evolve" else "pipeline",
                             source_name=(pdf.filename if pdf is not None else None))
    return {"job_id": job_id, "poll": f"/api/jobs/{job_id}"}


@app.get("/api/jobs")
async def list_jobs():
    """任务列表: 内存运行中任务 + 磁盘历史任务 (重启不丢)."""
    return jobs.list_jobs()


class BatchDeleteBody(BaseModel):
    ids: list[str]


@app.post("/api/jobs/delete-batch")
async def delete_jobs_batch(body: BatchDeleteBody):
    """批量删除任务. 必须注册在 /api/jobs/{job_id} 之前, 否则路径被通配吞掉."""
    if not body.ids:
        raise HTTPException(400, "ids 不能为空")
    return jobs.delete_jobs(body.ids)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    """删除单个任务 (运行中的任务拒绝删除)."""
    try:
        if jobs.delete_job(job_id):
            return {"deleted": job_id}
        raise HTTPException(404, "任务不存在")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    j = jobs.job_view(job_id)
    if j is None:
        raise HTTPException(404)
    return j


@app.get("/api/jobs/{job_id}/artifacts/{kind}")
async def artifact(job_id: str, kind: str, dl: bool = False):
    # 产物在磁盘上即允许下载 (重启后内存任务表为空, 但历史产物仍可用)
    if jobs.get_job(job_id) is None and not (jobs.OUTPUT / job_id).exists():
        raise HTTPException(404)
    p = jobs.artifact_path(job_id, kind)
    if p is None:
        raise HTTPException(404, f"无 {kind}")
    # 报告默认浏览器直接打开 (inline); dl=1 强制下载
    if kind == "report" and not dl:
        return FileResponse(p, media_type="text/html; charset=utf-8")
    return FileResponse(p, filename=f"{job_id}_{kind}{p.suffix}")


@app.get("/api/jobs/{job_id}/artifacts.zip")
async def artifacts_zip(job_id: str):
    """全部产物打包下载 (缓存到 job 目录, 产物更新后自动重建)."""
    import json, time, zipfile
    job_dir = jobs.OUTPUT / job_id
    if jobs.get_job(job_id) is None and not job_dir.exists():
        raise HTTPException(404)
    zip_path = job_dir / "_artifacts.zip"
    # 汇总全部可打包产物 (投影图不进产物清单, 同样不打包)
    kinds = ["glb", "stl", "step", "obj", "dxf", "gcode_mill", "gcode_turn",
             "report", "manifest", "spec"]
    items = []
    for k in kinds:
        p = jobs.artifact_path(job_id, k)
        if p is not None:
            items.append((k, p))
    if not items:
        raise HTTPException(404, "无可打包产物")
    newest = max(p.stat().st_mtime for _, p in items)
    if zip_path.exists() and zip_path.stat().st_mtime >= newest:
        return FileResponse(zip_path, filename=f"{job_id}_artifacts.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, p in items:
            zf.write(p, arcname=f"{job_id}_{k}{p.suffix}")
    return FileResponse(zip_path, filename=f"{job_id}_artifacts.zip")


# MCP SSE 挂在 /mcp 下; SDK 内部自动处理 root_path 前缀, 不要传 mount_path
app.mount("/mcp", mcp.sse_app())   # MCP SSE: http://127.0.0.1:8410/mcp/sse

# Web UI: 优先托管构建产物 (web/dist), 未构建时回退源码目录
_web_dist = ROOT / "web" / "dist"
if (_web_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
else:
    print("[WARN] web/dist 不存在 (未构建), 回退托管 web/ 源码目录; 请运行: cd web && npm run build")
    app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8410)
