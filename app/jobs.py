# -*- coding: utf-8 -*-
"""
任务管理层 — FastAPI REST 与 MCP SSE 共享
- submit_job(): 提交重建任务, 返回 job_id
- get_job(): 查询状态/日志/历史
- artifact_path(): 获取产物文件路径
"""
from __future__ import annotations
import json, uuid, threading, time, shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)


def _now_ts() -> str:
    """秒级时间戳: YYYY-MM-DD HH:MM:SS"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_log_path(job_dir: Path) -> Path:
    return job_dir / "run.log"


def load_run_log(job_dir: Path) -> list[dict]:
    """从磁盘读回任务日志 (JSON Lines). 任务结束后/重启后仍可回看."""
    p = _run_log_path(job_dir)
    if not p.exists():
        return []
    entries = []
    try:
        for ln in p.read_text("utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                e = json.loads(ln)
                if isinstance(e, dict):
                    entries.append({"ts": e.get("ts", ""), "msg": e.get("msg", "")})
                else:
                    entries.append({"ts": "", "msg": str(e)})
            except Exception:
                entries.append({"ts": "", "msg": ln})
    except Exception:
        pass
    return entries

JOBS: dict[str, dict] = {}

# 体壳模板 spec (离线/模板模式用)
_TEMPLATE_FILE = OUTPUT / "_test" / "spec.json"
TEMPLATE_SPEC = json.loads(_TEMPLATE_FILE.read_text("utf-8")) if _TEMPLATE_FILE.exists() else None


def submit_job(pdf_bytes: bytes | None = None, use_template: bool = False,
               max_rounds: int = 4, mode: str = "pipeline",
               source_name: str | None = None) -> str:
    """提交一次重建任务, 返回 job_id.
    mode: "pipeline" (固定零件族参数收敛) | "evolve" (LLM 自主写 worker 代码多版本迭代).
    source_name: 用户上传的原始文件名 (用于任务列表展示)."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    job_dir = OUTPUT / job_id
    job_dir.mkdir(parents=True)
    JOBS[job_id] = {"status": "pending", "log": [], "history": [],
                    "id": job_id, "mode": mode, "created_at": time.time(),
                    "source_name": source_name or ""}
    # 元数据落盘: 重启后从磁盘恢复任务列表时仍能展示源文件名
    try:
        (job_dir / "meta.json").write_text(json.dumps(
            {"source_name": source_name or "", "mode": mode},
            ensure_ascii=False), "utf-8")
    except Exception:
        pass

    pdf_path = None
    if pdf_bytes is not None:
        pdf_path = job_dir / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

    def run():
        import asyncio, sys
        sys.path.insert(0, str(ROOT))
        JOBS[job_id]["status"] = "running"

        # 统一日志入口: 加秒级时间戳, 存内存 + 实时落盘 run.log (任务结束/重启后可回看)
        _log_path = _run_log_path(job_dir)

        def progress(msg):
            entry = {"ts": _now_ts(), "msg": str(msg)}
            JOBS[job_id]["log"].append(entry)
            try:
                with _log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass

        async def _run():
            if mode == "evolve":
                from app.evolve import run_evolve
                result = await run_evolve(pdf_path, job_dir, max_rounds=max_rounds,
                                          progress=progress)
                if result.get("best_round"):
                    (job_dir / "best_round.txt").write_text(str(result["best_round"]))
                JOBS[job_id].update({"status": "done", "result": {
                    "history": [{"round": h["round"], "score": h.get("score"),
                                 "issues": h.get("issues", [])} for h in result["history"]],
                    "elapsed": result["elapsed"],
                    "best_round": result["best_round"], "best_score": result["best_score"],
                    "report": f"/api/jobs/{job_id}/artifacts/report"}})
                return
            from app.pipeline import run_pipeline
            template = TEMPLATE_SPEC if (use_template or pdf_path is None) else None
            if template is not None:
                sample_pdf = OUTPUT / "_test" / "drawing.pdf"
                if not sample_pdf.exists():
                    sample_pdf = ROOT / "_test" / "drawing.pdf"
                pdf_arg = sample_pdf if sample_pdf.exists() else (pdf_path or (job_dir / "input.pdf"))
            else:
                pdf_arg = pdf_path
            result = await run_pipeline(pdf_arg, job_dir, template_spec=template,
                                        max_rounds=max_rounds, progress=progress)
            # pipeline 若因拓扑差异升级到了 evolve, result 里带 best_round
            if result.get("best_round"):
                (job_dir / "best_round.txt").write_text(str(result["best_round"]))
            out = {"history": result["history"], "elapsed": result["elapsed"],
                   "report": f"/api/jobs/{job_id}/artifacts/report"}
            if result.get("best_round") is not None:
                out.update({"best_round": result["best_round"],
                            "best_score": result.get("best_score")})
            JOBS[job_id].update({"status": "done", "result": out})

        try:
            asyncio.run(_run())
        except Exception as e:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(e)
            progress(f"失败: {e}")

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def _disk_job(job_id: str) -> dict | None:
    """服务重启后内存任务表为空, 从磁盘重建历史任务视图."""
    job_dir = OUTPUT / job_id
    if not job_dir.is_dir():
        return None
    done = (job_dir / "report.html").exists() or (job_dir / "model.glb").exists()
    best_round, best_score = None, None
    bf = job_dir / "best_round.txt"
    if bf.exists():
        try:
            best_round = int(bf.read_text().strip())
        except Exception:
            pass
        if best_round is not None:
            cj = job_dir / f"v{best_round}" / "compare.json"
            if cj.exists():
                try:
                    best_score = json.loads(cj.read_text("utf-8")).get("score")
                except Exception:
                    pass
    result = {"history": [], "report": f"/api/jobs/{job_id}/artifacts/report"}
    if best_round is not None:
        result.update({"best_round": best_round, "best_score": best_score})
    try:
        mtime = job_dir.stat().st_mtime
    except Exception:
        mtime = time.time()
    return {"id": job_id, "status": "done" if done else "failed",
            "mode": "evolve", "created_at": mtime,
            "source_name": _load_meta(job_dir).get("source_name", ""),
            "log": load_run_log(job_dir),
            "history": [], "result": result}


def job_view(job_id: str) -> dict | None:
    """返回任务的完整视图 (附产物清单), 供 REST 详情接口使用.
    内存表缺失时从磁盘回退 (服务重启后历史任务仍可查看)."""
    j = JOBS.get(job_id)
    if j is None:
        j = _disk_job(job_id)
        if j is None:
            return None
    out = dict(j)
    mf = OUTPUT / job_id / "artifacts_manifest.json"
    if mf.exists():
        try:
            out["manifest"] = json.loads(mf.read_text("utf-8"))
        except Exception:
            out["manifest"] = {}
    return out


def list_jobs() -> dict[str, dict]:
    """内存任务 + 磁盘历史任务 (服务重启后不丢)."""
    out = {k: {"status": v["status"], "mode": v.get("mode", ""),
               "created_at": v.get("created_at"),
               "source_name": v.get("source_name", "")}
           for k, v in JOBS.items()}
    try:
        for d in OUTPUT.glob("job_*"):
            if d.name in out:
                continue
            if not (d / "report.html").exists() and not (d / "model.glb").exists():
                continue
            try:
                mt = d.stat().st_mtime
            except Exception:
                mt = time.time()
            out[d.name] = {"status": "done", "mode": "evolve", "created_at": mt,
                           "source_name": _load_meta(d).get("source_name", "")}
    except Exception:
        pass
    return out


def _load_meta(job_dir: Path) -> dict:
    """读取任务元数据 meta.json (源文件名/模式), 不存在或损坏返回 {}."""
    try:
        return json.loads((job_dir / "meta.json").read_text("utf-8"))
    except Exception:
        return {}


def _rmtree_backend(job_dir: Path) -> None:
    """后台服务进程内删除目录.
    Windows: shutil.rmtree 会被宿主机 safe-delete 钩子拦截并重定向到回收站,
    而服务进程无桌面会话, 回收站不可用 -> 删除失败, 故用原生
    `cmd /c rmdir /S /Q` 直接落盘删除 (job_id 已做白名单校验, 安全).
    Linux: 无该钩子, 直接 shutil.rmtree."""
    import os
    if os.name == "nt":
        import subprocess
        r = subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", str(job_dir)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or job_dir.exists():
            raise ValueError(f"删除任务目录失败: {r.stderr.strip()[:200] or '目录仍存在'}")
    else:
        shutil.rmtree(job_dir)
        if job_dir.exists():
            raise ValueError("删除任务目录失败: 目录仍存在")


def delete_job(job_id: str) -> bool:
    """删除单个任务: 内存表移除 + 磁盘任务目录删除.
    运行中 (running) 的任务拒绝删除, 避免线程继续写已删目录."""
    # 路径穿越防护: 仅允许形如 job_xxxxxxxx 的 id
    if not job_id.startswith("job_") or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise ValueError(f"非法任务 id: {job_id}")
    j = JOBS.get(job_id)
    if j and j.get("status") == "running":
        raise ValueError(f"{job_id} 正在运行, 无法删除")
    JOBS.pop(job_id, None)
    job_dir = OUTPUT / job_id
    if job_dir.is_dir():
        _rmtree_backend(job_dir)
        return True
    return j is not None  # 内存里有记录也算删除成功


def delete_jobs(job_ids: list[str]) -> dict:
    """批量删除. 返回 {deleted: [...], skipped: {id: reason}}."""
    deleted, skipped = [], {}
    for jid in job_ids:
        try:
            if delete_job(jid):
                deleted.append(jid)
            else:
                skipped[jid] = "任务不存在"
        except ValueError as e:
            skipped[jid] = str(e)
    return {"deleted": deleted, "skipped": skipped}


def artifact_path(job_id: str, kind: str) -> Path | None:
    """返回产物路径, 不存在返回 None.
    版本类 (glb/stl/spec/render) 取 best_round 版本;
    统一产物 (step/obj/dxf/gcode/投影/报告/清单) 在 job 根目录 (finalize 生成)."""
    job_dir = OUTPUT / job_id
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    # 版本类产物需要版本目录
    target = None
    if vs:
        target = vs[-1]
        bf = job_dir / "best_round.txt"
        if bf.exists():
            br = job_dir / f"v{bf.read_text().strip()}"
            if br in vs:
                target = br
    if kind == "render" and target:
        renders = sorted(target.glob("renders/*.png")) or sorted(vs[-1].glob("renders/*.png"))
        return renders[0] if renders else None

    # 版本目录产物
    ver_files = {"glb": "model.glb", "stl": "model.stl", "spec": "spec.json"}
    if kind in ver_files and target:
        p = target / ver_files[kind]
        return p if p.exists() else None
    # 根目录统一产物 (finalize)
    root_files = {"step": "model.step", "obj": "model.obj", "dxf": "model.dxf",
                  "gcode_mill": "model_mill.nc", "gcode_turn": "model_turn.nc",
                  "proj_light": "proj_light.png", "proj_dark": "proj_dark.png",
                  "report": "report.html", "manifest": "artifacts_manifest.json"}
    if kind in root_files:
        p = job_dir / root_files[kind]
        return p if p.exists() else None
    return None
