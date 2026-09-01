# -*- coding: utf-8 -*-
"""
Drawing2Model Agent — MCP SSE 服务 (供 StaffDeck / 任意 MCP 客户端调用)

端点 (默认 127.0.0.1:8411):
  SSE:        http://127.0.0.1:8411/sse
  messages:   http://127.0.0.1:8411/messages/

工具:
  drawing2model   提交工程图 PDF -> 3D 模型重建任务 (同步等待完成)
  d2m_status      查询任务状态/日志
  d2m_artifacts   获取产物文件路径 (glb/stl/spec/report)

StaffDeck MCP 配置示例 (~/.workbuddy/mcp.json):
  {
    "mcpServers": {
      "drawing2model": {
        "url": "http://192.168.15.110:8411/sse"
      }
    }
  }

运行: python mcp_server.py   (默认 8411, 可用 MCP_PORT 覆盖)
"""
from __future__ import annotations
import sys, os, json, time, base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP
from app import jobs

mcp = FastMCP(
    "drawing2model",
    instructions="工程图纸 -> 3D 模型重建服务。上传机械零件工程图 PDF, "
                 "返回参数化重建的 3D 模型 (GLB/STL) 与制造报告。",
)


def _wait_job(job_id: str, timeout: float = 600.0) -> dict:
    """轮询等待任务完成"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get_job(job_id)
        if j and j["status"] in ("done", "failed"):
            return j
        time.sleep(2.0)
    return jobs.get_job(job_id) or {"status": "timeout"}


@mcp.tool()
def drawing2model(pdf_base64: str = "", pdf_path: str = "",
                  use_template: bool = False, max_rounds: int = 3,
                  mode: str = "pipeline", wait: bool = True, timeout: int = 900) -> str:
    """从机械零件工程图 PDF 重建 3D 模型。

    输入 (二选一):
      pdf_base64: PDF 文件的 base64 编码 (优先)
      pdf_path:   PDF 文件在服务器上的绝对路径
      use_template: True 时用内置体壳模板参数 (跳过 VLM 识别, 离线可用)
      max_rounds: 最大迭代轮数 (1-5)
      mode: "pipeline"=固定零件族参数收敛; "evolve"=LLM 自主生成 worker 代码
            多版本迭代 (无需预置零件族, 任意图纸, 更慢但覆盖面广)
      wait:   True 时同步等待完成 (最长 timeout 秒); False 时立即返回 job_id
      timeout: 等待超时秒数

    返回: JSON 字符串, 含 status/job_id/迭代历史/产物路径/收敛结论
    """
    pdf_bytes = None
    if pdf_base64:
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            return json.dumps({"status": "error", "error": "pdf_base64 解码失败"}, ensure_ascii=False)
    elif pdf_path:
        p = Path(pdf_path)
        if not p.exists():
            return json.dumps({"status": "error", "error": f"文件不存在: {pdf_path}"}, ensure_ascii=False)
        pdf_bytes = p.read_bytes()
    elif not use_template:
        return json.dumps({"status": "error",
                           "error": "需提供 pdf_base64 或 pdf_path, 或设 use_template=true"},
                          ensure_ascii=False)

    job_id = jobs.submit_job(pdf_bytes=pdf_bytes, use_template=use_template,
                             max_rounds=max(1, min(5, max_rounds)),
                             mode="evolve" if mode == "evolve" else "pipeline",
                             source_name=(Path(pdf_path).name if pdf_path else None))
    if not wait:
        return json.dumps({"status": "submitted", "job_id": job_id,
                           "hint": "用 d2m_status(job_id) 查询进度"}, ensure_ascii=False)

    j = _wait_job(job_id, timeout=timeout)
    out = {"job_id": job_id, "status": j["status"], "log": j.get("log", [])}
    if j["status"] == "done":
        h = j["result"]["history"]
        last = h[-1]
        out["elapsed_s"] = round(j["result"]["elapsed"], 1)
        out["rounds"] = len(h)
        out["final_score"] = last.get("score")
        out["remaining_issues"] = len(last.get("issues", []))
        out["converged"] = len(last.get("issues", [])) == 0
        # 产物绝对路径 (MCP 客户端可直接读)
        out["artifacts"] = {}
        for kind in ("glb", "stl", "spec", "report", "render"):
            p = jobs.artifact_path(job_id, kind)
            if p:
                out["artifacts"][kind] = str(p.resolve())
        if last.get("issues"):
            out["issues_sample"] = last["issues"][:3]
    elif j["status"] == "failed":
        out["error"] = j.get("error", "")[:500]
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def d2m_status(job_id: str) -> str:
    """查询重建任务状态。返回 JSON: status/日志/迭代历史。"""
    j = jobs.get_job(job_id)
    if j is None:
        return json.dumps({"status": "error", "error": f"未知 job: {job_id}"}, ensure_ascii=False)
    out = {"job_id": job_id, "status": j["status"], "log": j.get("log", [])}
    if j["status"] == "done" and "result" in j:
        out["history"] = [{"round": r["round"], "score": r.get("score"),
                           "issues": len(r.get("issues", []))} for r in j["result"]["history"]]
    if j.get("error"):
        out["error"] = j["error"][:500]
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def d2m_artifacts(job_id: str) -> str:
    """获取已完成任务的产物文件路径 (glb/stl/spec/report/render)。"""
    j = jobs.get_job(job_id)
    if j is None:
        return json.dumps({"status": "error", "error": f"未知 job: {job_id}"}, ensure_ascii=False)
    if j["status"] != "done":
        return json.dumps({"status": j["status"], "hint": "任务尚未完成"}, ensure_ascii=False)
    out = {}
    for kind in ("glb", "stl", "spec", "report", "render"):
        p = jobs.artifact_path(job_id, kind)
        if p:
            out[kind] = str(p.resolve())
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def d2m_health() -> str:
    """服务健康检查: LLM 在线状态/模型/案例库数量。"""
    from llm.gateway import get_gateway, _resolve_config
    from app import case_library
    gw = get_gateway()
    cfg = _resolve_config()
    return json.dumps({
        "llm": gw.status(),
        "model_vl": cfg["model_vl"], "model_text": cfg["model_text"],
        "base_url": cfg["base_url"],
        "cases": len(case_library._load()),
        "jobs": jobs.list_jobs(),
    }, ensure_ascii=False)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MCP_PORT", "8411"))
    sse_app = mcp.sse_app()
    uvicorn.run(sse_app, host="0.0.0.0", port=port, log_level="info")
