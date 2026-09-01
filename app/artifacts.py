# -*- coding: utf-8 -*-
"""
finalize_artifacts — 任务完成后统一生成全部产物格式
输入: best 版本 model.stl + job_dir
产出 (写入 job_dir):
  model.stl / model.glb / model.step / model.obj / model.dxf
  proj_light.png / proj_dark.png (三视图投影, 不在产物面板展示)
  model_mill.nc / model_turn.nc (G-Code)
  artifacts_manifest.json
"""
from __future__ import annotations
import json, subprocess, time
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
# 跨平台运行时路径 (环境变量可覆盖, 见 app/runtime.py)
from app.runtime import UTIL_PY as DEFAULT_PY, worker_env


def _run_default(script: str, *args, timeout=240) -> dict:
    r = subprocess.run([DEFAULT_PY, str(AGENT_ROOT / "app" / script), *[str(a) for a in args]],
                       capture_output=True, text=True, timeout=timeout)
    return {"ok": r.returncode == 0, "out": r.stdout[-500:], "err": r.stderr[-500:]}


def _run_ocp(script: str, *args, timeout=240) -> dict:
    from app import harness
    r = subprocess.run([harness.PY, str(AGENT_ROOT / "app" / script), *[str(a) for a in args]],
                       capture_output=True, text=True, timeout=timeout, env=worker_env())
    return {"ok": r.returncode == 0, "out": r.stdout[-500:], "err": r.stderr[-500:]}


def finalize(job_dir: Path, stl: Path, family: str = "", spec: dict | None = None,
             progress=print) -> dict:
    """生成全部产物格式, 返回 {格式: {ok, file, size, elapsed}}."""
    job_dir = Path(job_dir); stl = Path(stl)
    if not stl.exists():
        return {}
    res = {}
    t = time.time()

    # 0) 自检结果落盘 (报告质量检验章节引用)
    try:
        from app.evolve import stl_selfcheck
        chk = stl_selfcheck(stl)
        (job_dir / "selfcheck.json").write_text(json.dumps(chk, ensure_ascii=False), "utf-8")
    except Exception:
        pass

    # 1) GLB
    glb = job_dir / "model.glb"
    if not glb.exists():
        r = _run_default("stl2glb.py", stl, glb)
        res["glb"] = {"ok": glb.exists() and glb.stat().st_size > 200,
                      "elapsed": round(time.time() - t, 1)}
        progress(f"[F] GLB: {'OK' if res['glb']['ok'] else '失败'}")
    else:
        res["glb"] = {"ok": True, "elapsed": 0}

    # 2) STEP (OCP)
    step = job_dir / "model.step"
    if not step.exists():
        try:
            r = _run_ocp("step_export.py", stl, step)
            ok = r["ok"] and step.exists() and step.stat().st_size > 1000
        except Exception as e:
            ok = False
        res["step"] = {"ok": ok, "elapsed": round(time.time() - t, 1)}
        progress(f"[F] STEP: {'OK' if ok else '失败'}")
    else:
        res["step"] = {"ok": True, "elapsed": 0}

    # 3) OBJ + DXF + 三视图投影 (default env)
    for name, args in [
        ("obj", ("convert.py", stl, job_dir)),   # convert 一次出 obj+proj+dxf
    ]:
        pass
    try:
        r = _run_default("convert.py", stl, job_dir)
        conv_ok = r["ok"]
    except Exception:
        conv_ok = False
    for fmt, fname in [("obj", "model.obj"), ("dxf", "model.dxf"),
                       ("proj_light", "proj_light.png"), ("proj_dark", "proj_dark.png")]:
        p = job_dir / fname
        res[fmt] = {"ok": p.exists() and p.stat().st_size > 100,
                    "elapsed": round(time.time() - t, 1)}
    progress(f"[F] OBJ/DXF/投影: {'OK' if conv_ok else '失败'}")

    # 4) G-Code
    try:
        r = _run_default("gcode.py", stl, job_dir, "--family", family or "")
        g_ok = r["ok"]
    except Exception:
        g_ok = False
    for fname in ("model_mill.nc", "model_turn.nc"):
        p = job_dir / fname
        if p.exists():
            res[fname.split(".")[0]] = {"ok": True, "elapsed": round(time.time() - t, 1)}
    progress(f"[F] G-Code: {'OK' if g_ok else '失败'}")

    # 汇总清单 (proj_light/proj_dark 仅生成文件, 不进入产物清单)
    manifest = {fmt: {"file": fname_of(job_dir, fmt), "ok": v["ok"],
                      "size": _size(job_dir, fmt)}
                for fmt, v in res.items() if fmt not in ("proj_light", "proj_dark")}
    (job_dir / "artifacts_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    progress(f"[F] 产物生成完成: {sum(1 for v in res.values() if v['ok'])}/{len(res)} 成功")
    return res


FNAME = {"glb": "model.glb", "step": "model.step", "obj": "model.obj", "dxf": "model.dxf",
         "proj_light": "proj_light.png", "proj_dark": "proj_dark.png",
         "model_mill": "model_mill.nc", "model_turn": "model_turn.nc"}

def fname_of(job_dir: Path, fmt: str) -> str | None:
    return FNAME.get(fmt)

def _size(job_dir: Path, fmt: str) -> int:
    n = FNAME.get(fmt)
    p = job_dir / n if n else None
    return p.stat().st_size if (p and p.exists()) else 0
