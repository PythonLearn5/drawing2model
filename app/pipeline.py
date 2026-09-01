# -*- coding: utf-8 -*-
"""
pipeline 编排 — Drawing2Model 六阶段流水线
S1 预处理 -> S2 识别 -> S3 PartSpec -> S4 建模 -> S5 收敛回路 -> S6 报告
- 在线: Qwen-VL 识别 + 视觉比对; 离线: 使用 spec 模板/规则验证 + 案例库修正
- 每轮版本落盘 output/<job_id>/v{n}/
"""
from __future__ import annotations
import json, subprocess, sys, shutil, hashlib, time
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
# 跨平台运行时路径 (环境变量可覆盖, 见 app/runtime.py)
from app.runtime import WORKER_PY as PY, OCP_PYTHONPATH as OCP_ENV, UTIL_PY

RECOGNIZE_SYSTEM = """你是机械工程图纸识别专家。分析工程图纸图像, 输出结构化 PartSpec JSON。

零件族 part_type: "box_housing"(箱体壳体) | "shaft"(阶梯轴) | "roller"(滚柱/丝杠滚柱) | "blade"(叶片) | "disc"(盘类)

不同零件族用不同的字段 (直接输出对应族的原生字段, 不要塞进 features):

[roller] 行星滚柱/滚珠丝杠滚柱:
 {"part_type":"roller","L":106,"body_L":94,"major_d":16.436,"minor_d":16.26,
  "lead":16,"pitch":1,"journal":{"d":8,"len":6}}
[shaft] 阶梯轴:
 {"part_type":"shaft","segments":[{"d":8,"len":6},{"d":16.4,"len":94},{"d":8,"len":6}],
  "grooves":[{"x":0,"w":2,"depth":1}]}
[blade] 叶片 (截面坐标表放样):
 {"part_type":"blade","span":500,"chord":200,
  "sections":[{"x_pct":[1.25,2.5,5,...],"y":[6.9,9.7,...],"twist_deg":0.0},...],
  "root":{"w":80,"t":60,"h":60}}

[box_housing] 箱体壳体 (用 features 列表):
{
 "part_type": "box_housing",
 "outer": {"L": 长, "W": 宽, "H": 高},
 "features": [
   {"type": "stepped_bore", "axis": "X",
    "segments": [{"d": 230, "len": 134, "z": 250}, {"d": 215, "len": 60, "z": 250}]},
   {"type": "flange", "d": 260, "t": 15,
    "holes": {"n": 8, "pcd": 220, "d": 6.8, "depth": 16}},
   {"type": "cavity_grid", "nx": 2, "ny": 2, "w": 190, "h": 215, "r": 10},
   {"type": "hole_pattern", "face": "top"|"bottom", "n": 3, "d": 10.2, "depth": 24, "x": [40, 80]},
   {"type": "slope", "deg": 15, "h": 140}
 ],
}

通用字段: "family_confidence": 0到1, "uncertain": ["看不清的尺寸描述"]

注意:
- 数值单位毫米; 孔径标注 φxxx; 长度 len 表该段沿轴向长度
- roller: major_d=螺纹大径, minor_d=滚道底直径, pitch=相邻槽距(螺距), lead=导程; journal=两端光轴颈
- blade: sections 每站 x_pct/y 点数须一致; y 为该弦向位置的总厚(成品尺寸); twist_deg 为叠合扭转角
- 看不清的尺寸不要猜, 写到 uncertain 里
- 只输出 JSON, 无其他解释文字."""


# ---------------- S1 预处理 ----------------
def s1_preprocess(pdf_path: Path, out_dir: Path) -> list[Path]:
    """PDF -> 总览 + 分块图"""
    import fitz
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    out_dir.mkdir(parents=True, exist_ok=True)
    imgs = []
    pix = page.get_pixmap(matrix=fitz.Matrix(0.4, 0.4))
    ov = out_dir / "overview.png"; pix.save(str(ov)); imgs.append(ov)
    zoom = 200/72.0
    for ix in range(4):
        for iy in range(2):
            clip = fitz.Rect(W*ix/4, H*iy/2, W*(ix+1)/4, H*(iy+1)/2)
            p = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            f = out_dir / f"tile_{ix}_{iy}.png"; p.save(str(f)); imgs.append(f)
    return imgs


# ---------------- S2+S3 识别 -> PartSpec ----------------
async def s2_recognize(imgs: list[Path], reference_text: str = "",
                       force_template: dict | None = None) -> dict:
    """S2 视觉识别 -> PartSpec.
    force_template: 若提供则跳过 VLM, 直接用模板 (用户显式指定 use_template 时用).
    P2 策略: 图与本地真值同喂 VLM, 真值仅参考, 以 VLM 识别为准. 但当用户显式指定 use_template
    (offline 或信任某样例), 直接用模板, 跳过 VLM, 避免 VLM 在缺上下文时输出残缺 spec."""
    from llm.gateway import get_gateway
    if force_template is not None:
        spec = json.loads(json.dumps(force_template))
        spec["_source"] = "template"
        return spec
    gw = get_gateway()
    if gw.online:
        blobs = [p.read_bytes() for p in imgs[:9]]  # token 控制: 总览+8块
        user = "识别这张工程图纸, 输出 PartSpec JSON。"
        if reference_text:
            user += f"\n本地提取的标注参考(仅供校对, 以你看到的为准):\n{reference_text}"
        spec = await gw.vision_json(blobs, RECOGNIZE_SYSTEM, user)
        if spec:
            # VLM 输出语义结构 (features[]), 经 spec_adapter 转为 PartSpec 工程结构
            try:
                from app.spec_adapter import vlm_to_partspec, validate_partspec
                adapted = vlm_to_partspec(spec)
                warnings = validate_partspec(adapted)
                if warnings:
                    adapted.setdefault("uncertain", []).extend(warnings)
                return adapted
            except Exception as e:
                spec["_source"] = "vlm_raw_no_adapt"
                spec.setdefault("uncertain", []).append(f"adapter 失败: {e}")
                return spec
    # 离线降级: 由调用方提供模板 spec
    return {"part_type": None, "_source": "offline", "uncertain": ["离线模式, 无法视觉识别"]}


# ---------------- S4 建模 worker (子进程, OCP 环境) ----------------
def s4_build(spec: dict, out_dir: Path, family: str = "box_housing") -> Path:
    """调用 families/<family>.py 子进程建模 -> STL, 再转 GLB, 渲染验证图"""
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_file = out_dir / "spec.json"
    spec_file.write_text(json.dumps(spec, ensure_ascii=False, indent=2), "utf-8")
    script = AGENT_ROOT / "families" / f"{family}.py"
    stl = out_dir / "model.stl"
    r = subprocess.run([PY, str(script), str(spec_file), str(stl)],
                       capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": OCP_ENV}, timeout=300)
    if r.returncode != 0 or not stl.exists():
        raise RuntimeError(f"建模失败: {r.stderr[-800:]}")
    # STL -> GLB
    r2 = subprocess.run([UTIL_PY, str(AGENT_ROOT/"app"/"stl2glb.py"),
                         str(stl), str(out_dir/"model.glb")], capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        raise RuntimeError(f"GLB转换失败: {r2.stderr[-500:]}")
    # 渲染4视角 (直接输出到 renders/)
    render_dir = out_dir / "renders"; render_dir.mkdir(exist_ok=True)
    rv = AGENT_ROOT / "app" / "render_views.py"
    subprocess.run([UTIL_PY, str(rv), str(stl), str(render_dir)],
                   capture_output=True, text=True, timeout=180)
    # 测量 (供规则校验)
    measured = _measure(stl)
    (out_dir / "measured.json").write_text(json.dumps(measured, indent=2), "utf-8")
    return stl


def _measure(stl: Path) -> dict:
    try:
        sys.path.insert(0, "")
        from stl import mesh as stlmesh
        import numpy as np
        m = stlmesh.Mesh.from_file(str(stl))
        v = m.vectors.reshape(-1, 3)
        mn, mx = v.min(0), v.max(0)
        return {"bbox": {"min": mn.tolist(), "max": mx.tolist()},
                "size": {"dx": float(mx[0]-mn[0]), "dy": float(mx[1]-mn[1]), "dz": float(mx[2]-mn[2])}}
    except Exception:
        return {"bbox": {}, "size": {}}


# ---------------- 全流程 ----------------
async def run_pipeline(pdf_path: Path, job_dir: Path, template_spec: dict | None = None,
                       truth: dict | None = None, max_rounds: int = 4, progress=print) -> dict:
    """端到端.
    truth: 可选真值基线. 离线模式规则校验用 truth vs spec; 在线时 VLM 视觉比对替代 truth."""
    from app import convergence
    from llm.gateway import usage_reset, usage_total
    usage_reset()   # 任务级 token 统计清零 (升级 evolve 时接续累计)
    job_dir.mkdir(parents=True, exist_ok=True)
    if truth is None and template_spec is not None:
        # 模板 spec 默认作真值基线 (e.g. 人工核对过的样例)
        truth = template_spec
    t0 = time.time()

    # S1
    progress("[S1] 图纸预处理...")
    imgs = s1_preprocess(pdf_path, job_dir)

    # S1.5 解析前无损高清化: 扫描型原样提取内嵌母版 / 矢量型高倍率渲染 (失败自动回退)
    hd_imgs = None
    try:
        from app.enhance import enhance_drawing
        meta = enhance_drawing(pdf_path, job_dir)
        ov_hd = meta.get("overview_hd")
        if ov_hd and Path(ov_hd).exists() and Path(ov_hd).stat().st_size > 2000:
            ov_p = Path(ov_hd)
            hd_imgs = [ov_p]
            # 矢量型: 总览+高清分块 (信息密度高); 扫描型: 单张高清全图已含全部信息
            if meta.get("source") == "vector":
                hd_imgs += sorted(job_dir.glob("tiles_hd_*.png"))[:4]
            progress(f"[S1.5] 无损高清化: {meta.get('source')} "
                     f"{meta.get('w')}x{meta.get('h')}"
                     + (" (短边限幅)" if meta.get("limited") else " (原样提取)"))
    except Exception as e:
        progress(f"[S1.5] 高清化异常, 回退标准预处理图: {e}")

    # S2+S3
    # 显式传入 template_spec 时强制走模板 (跳过 VLM); 否则让 VLM 识别
    force_template = template_spec if template_spec is not None else None
    progress(f"[S2] {'模板 spec' if force_template else '视觉识别 (Qwen-VL / 离线降级)'}...")
    spec = await s2_recognize(hd_imgs or imgs, force_template=force_template)
    if spec.get("part_type") is None and template_spec is None:
        raise RuntimeError("离线模式且无模板 spec; 请设置 LLM_API_KEY 或使用 use_template 模式")
    # 按 families/ 下实际存在的 worker 动态选择; 未实现的类型降级到首个可用
    available = sorted(p.stem for p in (AGENT_ROOT / "families").glob("*.py")
                       if not p.stem.startswith("_"))
    family = spec.get("family") or spec.get("part_type")
    if family not in available:
        if available:
            progress(f"[S4] 注意: worker '{family}' 未实现, 降级使用 '{available[0]}'")
            family = available[0]
        else:
            raise RuntimeError("families/ 下没有可用的建模 worker")

    # S5 循环 (S4 在内); 视觉比对用高清版图纸 (无则回退标准总览)
    history = []
    drawing_img = (job_dir / "overview.png").read_bytes()
    try:
        from app.enhance import enhance_drawing
        _m = enhance_drawing(pdf_path, job_dir)
        ov_hd = _m.get("overview_hd")
        if ov_hd and Path(ov_hd).exists() and Path(ov_hd).stat().st_size > 2000:
            drawing_img = Path(ov_hd).read_bytes()
    except Exception:
        pass
    # 可扩展迭代预算 (与 evolve 策略一致): 只有 分数>=阈值 且 无遗留问题 才允许收敛停止;
    # 基础预算用尽仍未收敛时自动扩展到硬上限 HARD_MAX_ROUNDS, 绝不因"到轮次上限"就低分交付.
    from app.evolve import HARD_MAX_ROUNDS
    budget = max(2, max_rounds)
    hard_cap = max(budget, HARD_MAX_ROUNDS)
    budget_extended = False
    converged = False
    rnd = 0
    while rnd < hard_cap:
        rnd += 1
        if rnd == budget + 1 and not budget_extended:
            budget_extended = True
            progress(f"[S5] 基础预算 {budget} 轮用尽仍未收敛 "
                     f"(分数<0.8 或有遗留问题), 自动扩展迭代预算至 {hard_cap} 轮")
        progress(f"[S4] 第{rnd}轮建模...")
        vdir = job_dir / f"v{rnd}"
        try:
            s4_build(spec, vdir, family)
        except RuntimeError as e:
            progress(f"  建模失败: {e}")
            break
        measured = json.loads((vdir/"measured.json").read_text("utf-8"))

        # 规则验证 (永远跑, 确定性; 离线时用 truth vs spec)
        issues = convergence.spec_rules_check(spec, measured, truth=truth, family=family)
        # 视觉验证 (在线时)
        render_paths = [vdir/"renders"/n for n in ["v_iso.png","v_front.png","v_side.png","v_top.png"]]
        render_blobs = [p.read_bytes() for p in render_paths if p.exists()]
        vres = await convergence.validate_round(render_blobs, drawing_img) if render_blobs else {"score": None}
        score = vres.get("score")
        vl_issues = vres.get("issues", [])
        all_issues = issues + vl_issues

        history.append({"round": rnd, "score": score, "issues": all_issues,
                        "rules_issues": len(issues), "vl_issues": len(vl_issues)})
        progress(f"  score={score} 规则差异={len(issues)} 视觉差异={len(vl_issues)}")

        stop, why = convergence.should_stop(score, rnd, budget, n_issues=len(all_issues))
        if stop:
            progress(f"  收敛停止: {why}")
            converged = True
            break
        if score is None and not all_issues:
            progress("  离线模式且无规则差异, 停止")
            converged = True
            break
        # 修正
        fix = await convergence.repair_spec(spec, all_issues, family)
        if fix.get("topology_issue"):
            history[-1]["topology_broken"] = True
            # 拓扑问题不能只改参数 -> 自动升级到 evolve: LLM 重写代码, 用布尔切除补特征
            from llm.gateway import get_gateway
            if get_gateway().online:
                topo_detail = (fix.get("detail") or "") + "\n差异清单:\n" + "\n".join(
                    f"- {i.get('part','')}: {i.get('symptom','')}" for i in all_issues[:10])
                progress(f"  检测到拓扑差异 (特征缺失/布尔错误), 参数修正无法覆盖, 自动升级 evolve 模式重写代码...")
                from app.evolve import run_evolve
                eres = await run_evolve(pdf_path, job_dir, max_rounds=max(3, max_rounds),
                                        progress=progress, topology_detail=topo_detail,
                                        round_offset=rnd)
                if eres.get("best_round"):
                    (job_dir / "best_round.txt").write_text(str(eres["best_round"]))
                history.extend([{"round": h["round"], "score": h.get("score"),
                                 "issues": h.get("issues", []),
                                 "stage": h.get("stage"), "evolve": True} for h in eres["history"]])
                tu = usage_total()
                if tu["calls"]:
                    progress(f"[E] 任务总 token 消耗: prompt={tu['prompt']} "
                             f"completion={tu['completion']} (共调用 {tu['calls']} 次)")
                return {"final_spec": {"part_type": family, "_source": "evolve_escalation",
                                       "topology_detail": fix.get("detail", "")[:500]},
                        "versions": [h["round"] for h in history], "history": history,
                        "report": str(eres.get("report_path")),
                        "best_round": eres.get("best_round"),
                        "best_score": eres.get("best_score"),
                        "elapsed": round(time.time() - t0, 1)}
            progress(f"  拓扑问题且 LLM 离线, 无法自主修正, 熔断转人工: {fix.get('detail')}")
            break
        params = fix.get("params", {})
        if not params:
            progress("  无参数可修, 停止")
            break
        spec = convergence.apply_params(spec, params)
        progress(f"  修正: {params}")

    if not converged and rnd >= hard_cap:
        progress(f"[S5] 达到硬上限 {hard_cap} 轮仍未完全收敛 "
                 f"(分数<0.8 或有遗留问题), 按当前最优版本交付")

    # S6 报告
    progress("[S6] 生成报告...")
    tu = usage_total()
    if tu["calls"]:
        progress(f"[S6] 任务总 token 消耗: prompt={tu['prompt']} completion={tu['completion']} "
                 f"(共调用 {tu['calls']} 次)")
    report_path = s6_report(job_dir, spec, history, family)
    return {"final_spec": spec, "versions": [h["round"] for h in history],
            "history": history, "report": str(report_path),
            "elapsed": round(time.time()-t0, 1), "token_usage": tu}


def s6_report(job_dir: Path, spec: dict, history: list, family: str) -> Path:
    from app.report import render_report
    return render_report(job_dir, spec, history, family)
