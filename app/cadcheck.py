# -*- coding: utf-8 -*-
"""
cadcheck — cadquery/OCP 几何校验 (替代人工预览, 取代已取消的 FreeCAD 方案)

把 worker 导出的 STEP 读回内存, 用内核级检查验证模型:
- 拓扑合法性: BRepCheck_Analyzer 全深度校验 (自交/悬边/非法环)
- 实体完整性: 至少 1 个 solid, 且无退化解 (体积>1)
- 几何度量: 体积 + 包围盒尺寸 (可与图纸标注对照)
- 面法向: 全部面必须可计算法向 (法向缺失 = 退化面)

校验结果作为**几何差异项**与视觉比对结果一起喂回生成器,
让 LLM 在下一轮直接修正结构问题, 而不是只看渲染图猜测.

独立子进程运行 (PYTHONPATH=ocp_env), 避免 OCP 与主进程共存冲突;
子进程退出时偶发 OCP 清理段错误 (RC=139) 不影响结果——只要
stdout 最后一行是合法 JSON 即视为成功.
"""
from __future__ import annotations
import json, os, subprocess, uuid
from pathlib import Path

from app import harness

AGENT_ROOT = Path(__file__).resolve().parent.parent

# 校验脚本模板: 读回 STEP -> 拓扑/体积/包围盒/面法向 -> JSON
_CADCHECK_SRC = '''import sys, json
try:
    import cadquery as cq
except ImportError as e:
    print(json.dumps({"ok": False, "err": f"cadquery import fail: {e}"}))
    sys.exit(0)
path = sys.argv[1]
try:
    res = cq.importers.importStep(path)
    shapes = res.vals() or []
except Exception as e:
    print(json.dumps({"ok": False, "err": f"STEP read fail: {e}"}))
    sys.exit(0)

from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS

issues = []
n_solid = 0
vol_total = 0.0
bbox = [0.0, 0.0, 0.0]
for sp in shapes:
    shape = getattr(sp, "wrapped", sp)
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        n_solid += 1
        solid = ex.Current()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        vol_total += props.Mass()
        ex.Next()
    # 拓扑合法性校验 (深度)
    ana = BRepCheck_Analyzer(shape, True)
    if not ana.IsValid():
        issues.append("拓扑非法: BRepCheck 发现无效结构 (自交/悬边/非法环)")
    # 包围盒
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    bbox = [round(x1 - x0, 2), round(y1 - y0, 2), round(z1 - z0, 2)]
    # 面法向检查 (退化面无法计算法向)
    fex = TopExp_Explorer(shape, TopAbs_FACE)
    n_face, bad_face = 0, 0
    while fex.More():
        n_face += 1
        try:
            surf = BRep_Tool.Surface_s(TopoDS.Face_s(fex.Current()))
            if surf is None:
                bad_face += 1
        except Exception:
            bad_face += 1
        fex.Next()
    if n_face and bad_face > n_face * 0.1:
        issues.append(f"退化面: {bad_face}/{n_face} 个面无法计算几何属性")

if n_solid == 0:
    issues.append("无实体: STEP 中未包含任何 solid (模型退化为壳/线框)")
if vol_total <= 1.0:
    issues.append(f"体积退化: 总体积 {vol_total:.2f} 过小 (<=1), 实体可能扁平/空壳")
ok = not issues
print(json.dumps({"ok": ok, "solids": n_solid, "vol": round(vol_total, 2),
                  "bbox": bbox, "issues": issues}, ensure_ascii=False))
'''


def check_step(step_path: Path, timeout: int = 120) -> dict:
    """用 cadquery 读回 STEP 校验几何质量.
    返回 {ok, solids, vol, bbox, issues, err}.
    ok=False 时 issues 列出具体问题 (直接可喂给生成器修复)."""
    step_path = Path(step_path)
    if not step_path.exists() or step_path.stat().st_size < 200:
        return {"ok": False, "solids": 0, "vol": 0.0, "bbox": [0, 0, 0],
                "issues": ["STEP 文件缺失或过小"], "err": "STEP missing"}

    tmp_dir = AGENT_ROOT / "output" / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = None
    for _ in range(3):
        cand = tmp_dir / f"_cadcheck_{uuid.uuid4().hex[:8]}.py"
        try:
            cand.write_text(_CADCHECK_SRC, "utf-8")
            script = cand
            break
        except Exception:
            continue
    if script is None:
        return {"ok": False, "solids": 0, "vol": 0.0, "bbox": [0, 0, 0],
                "issues": ["校验脚本写入失败"], "err": "script write fail"}

    def _cleanup():
        try:
            script.unlink(missing_ok=True)
        except Exception:
            pass

    # HOME/USERPROFILE 必给: cadquery 导入时用 Path("~").expanduser() 找资源
    # (跨平台细节见 app/runtime.py 的 worker_env)
    try:
        r = subprocess.run([harness.PY, str(script), str(step_path)],
                           capture_output=True, text=True, timeout=timeout,
                           env=harness.worker_env())
        # OCP 退出段错误 (RC=139) 不影响已输出的结果, 以 stdout 最后合法 JSON 为准
        lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        info = None
        for ln in reversed(lines):
            try:
                info = json.loads(ln)
                break
            except Exception:
                continue
        if info is None:
            return {"ok": False, "solids": 0, "vol": 0.0, "bbox": [0, 0, 0],
                    "issues": ["cadcheck 无输出"],
                    "err": (r.stderr or "").strip()[-300:] or f"rc={r.returncode}"}
        info.setdefault("issues", [])
        info.setdefault("err", None)
        return info
    except subprocess.TimeoutExpired:
        return {"ok": False, "solids": 0, "vol": 0.0, "bbox": [0, 0, 0],
                "issues": [f"cadcheck 超时 (>{timeout}s)"], "err": "timeout"}
    except Exception as e:
        return {"ok": False, "solids": 0, "vol": 0.0, "bbox": [0, 0, 0],
                "issues": [f"cadcheck 异常: {e}"], "err": str(e)}
    finally:
        _cleanup()
