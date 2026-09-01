# -*- coding: utf-8 -*-
"""S6 报告生成 — Jinja2 模板 (templates/d2m_report.html.j2)
头部: Drawing2Model; 无多产物切换; 无三视图验证闭环记录;
新增: 设备与工装 / 质量检验(图纸技术要求) / 图纸数据(截面/站位/厚度)."""
from __future__ import annotations
import json, base64, re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

AGENT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = AGENT_ROOT / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

FAMILY_LABEL = {"blade": "叶片", "roller": "行星滚柱", "shaft": "阶梯轴",
                "box_housing": "箱壳/体壳", "evolve": "LLM 自主重建"}

# 按族的默认图纸技术要求 (质量检验章节; 若 spec 里有 truth_requirements 优先)
DEFAULT_REQUIREMENTS = [
    ("未注尺寸公差按 IT12 级", "卡尺 / 三坐标"),
    ("未注形位公差按 GB/T 1184-K 级", "三坐标测量机"),
    ("表面粗糙度未注处 Ra3.2", "粗糙度仪"),
    ("去毛刺、锐边倒钝", "目视 / 手感检查"),
    ("零件不得有裂纹、锈蚀等缺陷", "目视检查"),
]


def _pick_stl(job_dir: Path) -> Path | None:
    bf = job_dir / "best_round.txt"
    if bf.exists():
        cand = job_dir / f"v{bf.read_text().strip()}" / "model.stl"
        if cand.exists():
            return cand
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    for v in reversed(vs):
        p = v / "model.stl"
        if p.exists():
            return p
    return None


def _pick_glb(job_dir: Path) -> Path | None:
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    bf = job_dir / "best_round.txt"
    if bf.exists():
        cand = job_dir / f"v{bf.read_text().strip()}" / "model.glb"
        if cand.exists() and cand.stat().st_size > 100:
            return cand
    # 统一产物 (finalize 生成在根目录)
    root_glb = job_dir / "model.glb"
    if root_glb.exists() and root_glb.stat().st_size > 100:
        return root_glb
    for v in reversed(vs):
        g = v / "model.glb"
        if g.exists() and g.stat().st_size > 100:
            return g
    return None


def _best_spec(job_dir: Path) -> dict:
    """优先读 best 版本的 spec, 否则读最后一版."""
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    bf = job_dir / "best_round.txt"
    cands = []
    if bf.exists():
        cands.append(job_dir / f"v{bf.read_text().strip()}" / "spec.json")
    cands += [v / "spec.json" for v in reversed(vs)]
    for c in cands:
        if c.exists():
            try:
                return json.loads(c.read_text("utf-8"))
            except Exception:
                pass
    return {}


def _best_measured(job_dir: Path) -> dict:
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    bf = job_dir / "best_round.txt"
    cands = []
    if bf.exists():
        cands.append(job_dir / f"v{bf.read_text().strip()}" / "measured.json")
    cands += [v / "measured.json" for v in reversed(vs)]
    for c in cands:
        if c.exists():
            try:
                return json.loads(c.read_text("utf-8"))
            except Exception:
                pass
    return {}


_PARAM_RE = re.compile(r"^\s*([A-Z][A-Z0-9_,\s]*[A-Z0-9_])\s*=\s*([^#\n]+?)(?:\s*#\s*(.*))?\s*$")


def _best_worker(job_dir: Path) -> Path | None:
    bf = job_dir / "best_round.txt"
    if bf.exists():
        cand = job_dir / f"v{bf.read_text().strip()}" / "worker.py"
        if cand.exists():
            return cand
    vs = sorted([p for p in job_dir.glob("v*") if p.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]))
    for v in reversed(vs):
        c = v / "worker.py"
        if c.exists():
            return c
    return None


def _worker_params(job_dir: Path) -> list:
    """evolve 模式回退: 从 best 版 worker.py 提取参数块 (名称/数值/注释)."""
    wp = _best_worker(job_dir)
    if wp is None:
        return []
    rows, started = [], False
    try:
        lines = wp.read_text("utf-8").split("\n")
    except Exception:
        return []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith(("import ", "from ")):
            continue
        m = _PARAM_RE.match(ln)
        name = m.group(1).strip() if m else None
        if m and name and all(c.isupper() or c.isdigit() or c in ",_ " for c in name):
            val = m.group(2).strip()
            cmt = (m.group(3) or "").strip()
            rows.append((name, f"{val}" + (f"  ({cmt})" if cmt else "")))
            started = True
            continue
        if started:  # 参数块结束
            break
    # 建模备注
    notes = ""
    bf = job_dir / "best_round.txt"
    cands = []
    if bf.exists():
        cands.append(job_dir / f"v{bf.read_text().strip()}" / "notes.json")
    cands.append(wp.parent / "notes.json")
    for c in cands:
        if c.exists():
            try:
                notes = (json.loads(c.read_text("utf-8")).get("notes") or "").strip()
            except Exception:
                pass
            if notes:
                break
    if notes:
        rows.append(("建模备注", notes[:160]))
    return rows


def _drawing_rows(spec: dict) -> list:
    """图纸数据: 外形/主尺寸/站位/厚度等."""
    rows = []
    fam = spec.get("part_type") or spec.get("family") or ""
    if fam == "blade":
        secs = spec.get("sections") or []
        rows.append(("截面数量", f"{len(secs)} 个站位"))
        if spec.get("span"): rows.append(("叶高 (span)", f"{spec['span']} mm"))
        if spec.get("chord"): rows.append(("弦长 (chord)", f"{spec['chord']} mm"))
        if spec.get("twist_deg"): rows.append(("扭转角", f"{spec['twist_deg']}°"))
        for i, s in enumerate(secs[:6]):
            if isinstance(s, dict):
                z = s.get("z", s.get("station", i))
                pts = s.get("points") or s.get("coords") or []
                rows.append((f"站位 {i + 1} (z={z})", f"{len(pts)} 个截面坐标点"))
        if len(secs) > 6:
            rows.append(("...", f"共 {len(secs)} 个站位 (详见截面坐标表)"))
    elif fam == "roller":
        for k, label in [("L", "总长"), ("body_L", "滚道段长"), ("major_d", "大径 φ"),
                         ("minor_d", "小径 φ"), ("pitch", "螺距"), ("lead", "导程"),
                         ("groove_depth", "滚道槽深")]:
            if spec.get(k) is not None:
                rows.append((label, f"{spec[k]} mm"))
    elif fam == "shaft":
        for i, seg in enumerate((spec.get("segments") or [])[:8]):
            rows.append((f"轴段 {i + 1}", f"φ{seg.get('d', '?')} × {seg.get('len', seg.get('L', '?'))} mm"))
    elif fam == "box_housing":
        o = spec.get("outer", {})
        for k, label in [("L", "长 L"), ("W", "宽 W"), ("H", "高 H")]:
            if o.get(k): rows.append((f"外形 {label}", f"{o[k]} mm"))
        if o.get("slope_deg"): rows.append(("斜面", f"{o['slope_deg']}° / 高 {o.get('slope_h', '-')}"))
        if spec.get("bores"):
            rows.append(("轴承孔数量", f"{len(spec['bores'])} 个"))
        fl = spec.get("flange") or {}
        if fl.get("holes"):
            h = fl["holes"]
            rows.append(("法兰孔", f"{h.get('n', '?')}×φ{h.get('d', '?')} PCD {h.get('pcd', '?')}"))
    # evolve 通用: 把常见数值字段列出
    if not rows:
        for k, v in spec.items():
            if k.startswith("_") or k in ("part_type", "family"):
                continue
            if isinstance(v, (int, float, str)):
                rows.append((str(k), str(v)))
    return rows


def _sections_table(spec: dict) -> dict | None:
    """叶片截面坐标表."""
    if (spec.get("part_type") or spec.get("family")) != "blade":
        return None
    secs = spec.get("sections") or []
    if not secs:
        return None
    header, rows = ["站位", "x", "y", "厚度(相邻间距)"], []
    prev_z = None
    for i, s in enumerate(secs[:12]):
        if not isinstance(s, dict):
            continue
        z = s.get("z", s.get("station", i))
        thick = "" if prev_z is None else f"{abs(float(z) - float(prev_z)):.2f}"
        pts = s.get("points") or s.get("coords") or []
        if pts and isinstance(pts[0], (list, tuple)) and len(pts[0]) >= 2:
            x, y = pts[0][0], pts[0][1]
        else:
            x = y = "-"
        rows.append([f"S{i + 1} (z={z})", x, y, thick])
        prev_z = z
    return {"header": header, "rows": rows} if rows else None


def _gcode_info(job_dir: Path) -> dict | None:
    """读取 finalize 生成的 G-Code 与设备工装信息."""
    from app import gcode as gcode_mod
    for key, fname in [("mill", "model_mill.nc"), ("turn", "model_turn.nc")]:
        p = job_dir / fname
        if p.exists():
            content = p.read_text("utf-8")
            # 重新生成以获取设备/刀具元数据 (与文件同源)
            try:
                from stl import mesh as stlmesh
            except ImportError:
                pass
            return {"kind": f"gcode_{key}", "name": fname, "content": content,
                    "lines": content.count("\n") + 1}
    return None


def render_report(job_dir: Path, spec: dict, history: list, family: str,
                  elapsed_sec: float | None = None) -> Path:
    job_dir = Path(job_dir)
    job_id = job_dir.name
    best_spec = _best_spec(job_dir) or {}
    merged = {**best_spec, **{k: v for k, v in spec.items()
                              if not k.startswith("_") and v is not None}}
    fam = merged.get("part_type") or merged.get("family") or family or "unknown"

    # 产物清单 (工具栏下载按钮)
    ARTS = [
        ("glb", "model.glb", "GLB"), ("stl", "model.stl", "STL"),
        ("step", "model.step", "STEP"), ("obj", "model.obj", "OBJ"),
        ("dxf", "model.dxf", "DXF"),
        ("gcode_mill", "model_mill.nc", "NC"), ("gcode_turn", "model_turn.nc", "NC"),
    ]
    artifacts = []
    for kind, fname, ext in ARTS:
        if (job_dir / fname).exists() or (kind in ("glb", "stl")):
            artifacts.append({"kind": kind, "label": fname, "ext": ext})

    # GLB
    glb_path = _pick_glb(job_dir)
    glb_b64 = base64.b64encode(glb_path.read_bytes()).decode() if glb_path else ""

    # 三维度量: 优先 measured.json, 否则直接从 STL 计算
    measured = _best_measured(job_dir)
    bbox = None
    volume_cm3 = None
    s = measured.get("size")
    vol = measured.get("volume")
    if not (isinstance(s, (list, tuple)) and len(s) == 3):
        # evolve 版本无 measured.json -> 从 best STL 计算
        bf = job_dir / "best_round.txt"
        stl_c = (job_dir / f"v{bf.read_text().strip()}" / "model.stl") if bf.exists() else None
        if stl_c is None or not stl_c.exists():
            stl_c = _pick_stl(job_dir)
        if stl_c and stl_c.exists():
            try:
                from stl import mesh as stlmesh
                m = stlmesh.Mesh.from_file(str(stl_c))
                mn = m.vectors.reshape(-1, 3).min(0); mx = m.vectors.reshape(-1, 3).max(0)
                s = (mx - mn).tolist()
                vol = float(m.get_mass_properties()[0]) if hasattr(m, "get_mass_properties") else None
            except Exception:
                s = None
    if isinstance(s, (list, tuple)) and len(s) == 3:
        bbox = f"{s[0]:.0f} × {s[1]:.0f} × {s[2]:.0f} mm"
    if vol:
        volume_cm3 = f"{abs(vol) / 1000:.1f}"

    # 迭代历史
    history_rows = []
    for h in history:
        score = h.get("score")
        stage = "执行失败" if h.get("stage") == "exec_fail" else (
            "参数建模" if "stage" not in h else ("自愈修复" if h.get("heal") else "代码重建"))
        if h.get("topology_broken"):
            stage = "拓扑差异→升级"
        issues = h.get("issues", [])
        detail = "; ".join(str(i.get("symptom", ""))[:36] for i in issues[:2]) or "—"
        history_rows.append({"round": h.get("round"), "score_txt": f"{score:.2f}" if score is not None else "—",
                             "stage_txt": stage, "detail": detail})

    # 设备与工装 + G-Code
    gc = _gcode_info(job_dir)
    equipment, fixture, tools = "—", "—", []
    if gc:
        # 从 finalize 生成的同一来源重算元数据
        try:
            stl = None
            bf = job_dir / "best_round.txt"
            if bf.exists():
                stl = job_dir / f"v{bf.read_text().strip()}" / "model.stl"
            if stl and stl.exists():
                from app import gcode as gmod
                gen = gmod.generate(stl, fam if fam != "unknown" else "", merged)
                for v in gen.values():
                    equipment, fixture, tools = v["equipment"], v["fixture"], v["tools"]
                    break
        except Exception:
            pass
    gcode_preview, gcode_lines, gcode_kind, gcode_name = None, 0, "", ""
    if gc:
        lines_all = gc["content"].split("\n")
        preview_n = 26
        import html as _html
        gcode_preview = _html.escape("\n".join(lines_all[:preview_n]))
        gcode_lines = gc["lines"]; gcode_kind = gc["kind"]; gcode_name = gc["name"]

    # 质量检验 (图纸技术要求)
    quality_rows = DEFAULT_REQUIREMENTS
    if merged.get("truth_requirements"):
        req = merged["truth_requirements"]
        if isinstance(req, list):
            quality_rows = [(str(r), "按图纸") for r in req]
        elif isinstance(req, str):
            quality_rows = [(req, "按图纸")]
    selfcheck = json.loads((job_dir / "selfcheck.json").read_text("utf-8")) \
        if (job_dir / "selfcheck.json").exists() else None
    quality_passed = bool(selfcheck and selfcheck.get("ok")) if selfcheck else True
    quality_note = (selfcheck or {}).get("err") or "见自检日志"

    best_round = spec.get("best_round")
    if best_round is None and (job_dir / "best_round.txt").exists():
        try:
            best_round = int((job_dir / "best_round.txt").read_text().strip())
        except Exception:
            pass
    best_score = spec.get("best_score")

    # logo (项目根目录 logo.png, base64 内嵌)
    logo_b64 = ""
    logo_p = AGENT_ROOT / "logo.png"
    if logo_p.exists():
        try:
            logo_b64 = base64.b64encode(logo_p.read_bytes()).decode()
        except Exception:
            logo_b64 = ""

    ctx = {
        "job_id": job_id,
        "title": f"{FAMILY_LABEL.get(fam, fam)}零件 · 3D 重建与制造报告",
        "family_label": FAMILY_LABEL.get(fam, fam),
        "mode": spec.get("_source", family),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "best_round": best_round, "best_score": best_score,
        "elapsed": (f"{elapsed_sec:.0f}s" if elapsed_sec else f"{len(history)} 轮"),
        "history": history, "history_rows": history_rows,
        "bbox": bbox, "volume_cm3": volume_cm3,
        "artifacts": artifacts, "glb_b64": glb_b64,
        "logo_b64": logo_b64,
        "zip_url": f"/api/jobs/{job_id}/artifacts.zip",
        "drawing_rows": _drawing_rows(merged) or _worker_params(job_dir),
        "sections_table": _sections_table(merged),
        "equipment": equipment, "fixture": fixture, "tools": tools,
        "quality_rows": quality_rows, "quality_passed": quality_passed,
        "quality_note": quality_note,
        "gcode_preview": gcode_preview, "gcode_lines": gcode_lines,
        "gcode_kind": gcode_kind, "gcode_name": gcode_name,
        "gcode_preview_lines": 26,
        "meta_rows": [("job", job_id), ("family", fam),
                      ("best_round", best_round), ("best_score", best_score),
                      ("spec 来源", spec.get("_source", "?"))],
        "three_js": (TEMPLATE_DIR / "vendor" / "three.min.js").read_text("utf-8"),
        "gltf_js": (TEMPLATE_DIR / "vendor" / "GLTFLoader.js").read_text("utf-8"),
    }
    html = _env.get_template("d2m_report.html.j2").render(**ctx)
    out = job_dir / "report.html"
    out.write_text(html, "utf-8")
    return out
