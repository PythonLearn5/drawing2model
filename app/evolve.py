# -*- coding: utf-8 -*-
"""
evolve — LLM 自主生成 worker 代码 + harness 沙箱执行 + VLM 视觉反馈多版本迭代
目标: 无需预置零件族, 对任意零件图纸自主逼近"完全符合图纸"的 3D 模型.

闭环 (每版本 v{n}):
  1) 生成: VLM 看 [原图纸, (上轮渲染), 上轮差异] -> 输出自包含 OCP worker 代码
  2) 执行: harness 静态安全扫描 + 受限子进程 -> STL
  3) 反馈: 多视角渲染 -> VLM 视觉比对 (图纸 vs 渲染) -> score + issues
  4) 迭代: 把 [图纸, 本轮渲染, issues, 本轮代码, stderr] 喂回生成器修代码
  5) 收敛: score>=0.9 直接交付 (可携遗留问题, 几何硬伤除外) 或
     score>=阈值且无 issues -> 选最优版本出 GLB + 报告

worker 代码契约 (LLM 必须遵守):
- 自包含, 只用 OCP/math/sys/json (禁 os/subprocess/open/socket/eval)
- 入口: python worker.py <out.stl>
- 结尾: BRepMesh_IncrementalMesh + StlAPI_Writer 写 argv[1], print 体积 json
"""
from __future__ import annotations
import json, os, subprocess, sys, shutil, time
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
# 跨平台运行时路径 (环境变量可覆盖, 见 app/runtime.py)
from app.runtime import UTIL_PY

GEN_SYSTEM = """你是资深 CAD 开发工程师。给你一张机械工程图纸, 你要写一个**自包含**的
Python worker 脚本, 参数化重建该零件的 3D 实体。

【目的】这段代码会被立即执行, 执行成功并生成**几何结构接近图纸**的实体是唯一目标。
后续有两道校验: ① 几何校验——cadquery 读回你导出的 STEP, 用内核检查拓扑合法性、
实体体积与包围盒尺寸, 发现问题会作为差异项喂回给你修正; ② 视觉比对——渲染图与
图纸逐项核对。所以轮廓比例、特征数量与位置必须尽量忠实图纸。

【建模栈 (优先用高层库, 少踩底层坑)】
- **首选 cadquery** (`import cadquery as cq`): Workplane/box/circle/extrude/revolve/
  loft/sweep/cut/fillet/chamfer, 布尔直接用 `result = a.cut(b)`; 闭合截面用
  cq.Workplane().polyline(pts).close() 或 spline/arc; 放样用 loft([wire1, wire2...])。
  cadquery 代码短、参数顺序直观、几乎不会写出非法拓扑——**能用 cadquery 表达的
  特征一律优先用它**, 而不是裸 OCP。
- numpy 可用 (坐标表/截面点/插值计算), math/json/sys 等标准库可用。
- 底层兜底: OCP (OpenCascade) 也可直接用 (cadquery 无法表达的精细操作时)。

你对代码有**完全的自由**: 建模思路、结构、特征顺序、库的选择都可任意,
唯一不可变的是下面的硬性契约。

硬性契约 (违反会导致执行被拒或无法校验):
1. import 范围: cadquery, numpy, OCP.* 以及安全标准库 (math, sys, json, itertools,
   functools, random, dataclasses 等), open() 可用于写文件。禁止系统级危险调用:
   os.system/subprocess/socket/网络库/eval/exec/__import__/ctypes/multiprocessing。
2. 入口: `python worker.py <out.stl> [out.step]`, 最终 STL 写 sys.argv[1],
   **同时必须把同一实体导出 STEP 到 sys.argv[2] (几何校验依赖它, 必写)**。
3. 结尾固定写法 (cadquery 路径):
   shape = result.val().wrapped if hasattr(result, 'val') else result   # 取 TopoDS_Shape
   cq.exporters.export(result, sys.argv[2])                              # STEP (必写)
   from OCP.BRepMesh import BRepMesh_IncrementalMesh
   from OCP.StlAPI import StlAPI_Writer
   BRepMesh_IncrementalMesh(shape, 0.2, False, 0.25, True)
   StlAPI_Writer().Write(shape, sys.argv[1])                             # STL (必写)
   (若全程用裸 OCP 建模: STEP 用 OCP.STEPControl.STEPControl_Writer().Transfer/Write
   写 sys.argv[2], STL 同上。)
4. 负特征 (孔/槽/腔) 用切除 (cq cut / BRepAlgoAPI_Cut), 正特征 (凸台/法兰/筋) 用合并。
5. 图纸尺寸标注 (φ/长度/角度/螺纹规格/坐标表) 要尽量读准并用于参数;
   看不清的做合理工程默认并在 notes 说明。
6. 只输出 JSON: {"code": "完整 python 源码", "notes": "建模思路与不确定项"}。

【运行时正确性 (务必逐条自查, 历史高频失败点)】
- 禁止除零: 分母 (长度/半径/段数) 必须先断言 >0; 可能为零的量 (弦长/厚度) 用 max(x, eps)。
- cadquery: extrude/revolve 前确认截面已 `.close()`; 参数顺序是 extrude(高度)、
  hole(直径)、circle(半径); 多个 Workplane 布尔用 `a.union(b)`/`a.cut(b)` 不要混用 OCP 布尔。
- cadquery 倒角/圆角 (高频崩溃点, 务必按此写, 否则必报 BRep_API 错误):
  `Workplane.chamfer(d)` / `.fillet(r)` **不接受 edges= 关键字参数**;
  正确用法是先选边再链式调用: `result = result.edges(">Z").chamfer(1.5)`
  (`.edges()` 用选择器选目标边, 紧跟 `.chamfer()`/`.fillet()`)。
  选择器: `>Z`/`<Z` 选最上/下, `|X`/`|Y`/`|Z` 选轴向边, `%plane` 选平面边。
  **切勿**用 `.filter(lambda e: ...)` 选边——`.edges()` 返回 Workplane 不是 Edge 列表,
  其元素无 `.Length()` 方法; 也**切勿**对键槽/孔内壁边倒角, 会触发拓扑失败。
  不确定能否正确选边时, 用 try/except 包住, 失败则跳过倒角保主体。
- OCP: gp_Pnt 取值必须带括号 p.X()/p.Y()/p.Z(); 构造器先轴系后尺寸
  (MakeCylinder(gp_Ax2, R, H)); TColgp_HArray1OfPnt 下标从 1 开始;
  类型转换用静态方法 TopoDS.Edge_s()/Face_s()/Wire_s() (严禁构造器转换)。
- 放样: 至少 2 个**闭合且不共面**的截面 (cadquery loft 同样要求)。
- 防御式编程: 不确定的特征 (圆角/倒角/复杂曲面) 用 try/except 跳过,
  保证主体一定出实体; 宁可特征少也不能整体崩溃。
- 写代码前心算一遍主流程数值: 段数、弧长、截面点数, 确认无 0/负值/越界。"""

FIX_SYSTEM = """你是资深 CAD 开发工程师。上一版 worker 代码重建的 3D 模型与图纸有差异。
给你: [0]=原图纸, [1..N]=当前模型多视角渲染, 以及文本形式的差异描述 (含几何校验结果:
拓扑合法性/实体体积/包围盒尺寸)、执行错误、上版代码。
输出**修复后的完整 worker 代码** (契约同生成阶段), JSON: {"code": "...", "notes": "..."}。
优先修: 几何校验报错 (拓扑非法/体积退化/无实体) -> 轮廓比例/主尺寸 ->
缺失的孔/槽/腔 (切除) 或凸台 (合并) -> 圆角倒角等次要特征。
若执行报错 (stderr 非空), 先修代码错误 (API 名/参数/拓扑无效)。
建模首选 cadquery (契约同生成阶段), 结尾必须同时写 STL(sys.argv[1]) 与 STEP(sys.argv[2])。
你对代码有完全的自由: 任意调整结构、更换建模思路、重写全部逻辑。"""

# 轮内即时修复: 执行失败时不直接丢弃该轮, 立即把完整报错喂回 LLM 重写 (最多 INNER_REPAIR_MAX 次)
INNER_REPAIR_MAX = 3
# 收敛硬上限: 基础预算 (max_rounds) 未收敛时自动扩展到此值, 防止无限迭代烧钱
HARD_MAX_ROUNDS = 12
# 快速交付线: 分数达到该值时即使仍有遗留问题也允许直接交付 (视觉比对已高度一致,
# 剩余多为次要差异, 不值得继续烧迭代轮次); 几何硬伤 (拓扑非法/体积退化/无实体) 除外——
# 那种情况没有可交付的实体, 且交付前自测会拦截。
DELIVER_SCORE = 0.9
INNER_FIX_SYSTEM = """你是资深 CAD 开发工程师。下面的 worker 代码执行失败了。
【目的】修复后代码必须能成功执行并产出体积为正的实体, 且几何结构尽量接近图纸——
执行成功是底线, 结构正确才是目标。修复时优先定位报错根因, 而不是盲目小改。

你对代码有**完全的自由**: 可以整体重写、更换建模策略 (放样/扫掠/布尔/基元组合均可)、
删除可疑特征, 只要最终能出正确结构的实体。**优先改用/简化为 cadquery 写法**——
它的参数顺序直观、不易写出非法拓扑, 比裸 OCP 更少踩坑。

常见报错类型与对策:
- ZeroDivisionError: 分母为零。检查长度/半径/段数变量, 用 max(x, 1e-6) 保护, 或改用合理默认值。
- TypeError: method 与数值运算。多半是 gp_Pnt 取值漏了括号——必须 p.X()/p.Y()/p.Z()。
- TypeError 参数顺序: 构造器参数顺序错, 如 MakeCylinder(Axes, R, H) 先轴系再半径再高;
  改用 cadquery 可基本避免这类问题。
- TypeError: got an unexpected keyword argument 'edges': `Workplane.chamfer()`/`.fillet()`
  **不接受 edges= 参数**。正确写法: `result = result.edges(">Z").chamfer(1.5)`
  (先 `.edges(选择器)` 选边, 再链式 `.chamfer(d)`)。把 `edges=` 去掉, 改用选择器选边。
- AttributeError: 'Workplane' object has no attribute 'Length' / 'Radius': `.edges().filter()`
  返回 Workplane 不是 Edge 对象。不要对 `.edges()` 的结果调 `.Length()`; 改用 cadquery
  选择器 (`>Z`/`<Z`/`|X`/`%plane`) 选边, 直接链式 `.chamfer()`/`.fillet()`。
- BRep_API: command not done / 拓扑构造失败: 检查截面是否闭合/共面, 或简化该特征。
  倒角/圆角引发此错误时, 多半选错了边 (如选到键槽/孔内壁边)。改用更精确的选择器,
  或用 try/except 跳过该倒角保主体。
- AttributeError/ImportError: 用了不存在的 API 或库, 核对函数名与所属模块。

输出契约: 允许 cadquery / numpy / OCP.* 与安全标准库及 open(),
结尾必须**同时**写 STL 到 sys.argv[1]、STEP 到 sys.argv[2]
(cadquery: cq.exporters.export(result, sys.argv[2]); STL 用 BRepMesh + StlAPI_Writer),
输出 JSON {"code": "完整源码", "notes": "..."})。
cadquery 易错点: extrude 前截面必须 .close(); hole(直径)/circle(半径) 别混淆;
loft 至少 2 个闭合不共面截面; 布尔用 .cut/.union 方法。
OCP 易错点: TopoDS 类型转换用静态方法 TopoDS.Edge_s()/Face_s()/Wire_s();
放样 ThruSections 至少 2 个闭合且不共面 wire; 布尔两操作数都必须是有效实体。"""

TOPO_GEN_SYSTEM = GEN_SYSTEM + """

特别要求 (拓扑升级): 上一版参数化建模在**特征拓扑**上出了错, 你写代码时必须
逐条落实下面的特征清单, 负特征 (孔/腔/槽/窗口/切角) 一律用布尔切除
(cadquery `.cut()` 或 BRepAlgoAPI_Cut), 凸台/法兰/加强筋用合并 (`.union()`/Fuse)。
宁可特征粗糙也不能少特征。"""


def _renders(stl: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([UTIL_PY,
                    str(AGENT_ROOT / "app" / "render_views.py"), str(stl), str(out_dir)],
                   capture_output=True, text=True, timeout=180)
    return sorted(out_dir.glob("*.png"))


def _glb(stl: Path, glb: Path):
    subprocess.run([UTIL_PY,
                    str(AGENT_ROOT / "app" / "stl2glb.py"), str(stl), str(glb)],
                   capture_output=True, text=True, timeout=120)


def glb_ok(glb: Path) -> bool:
    """GLB 最低有效性: 存在 + 体积 + 'glTF' magic."""
    if not glb.exists() or glb.stat().st_size < 200:
        return False
    return glb.read_bytes()[:4] == b"glTF"


def stl_selfcheck(stl: Path) -> dict:
    """OCP 读回 STL 计算体积, 判定模型是否退化.
    返回 {ok, vol, err}. 体积<=0 或不可读 = 退化."""
    import uuid as _uuid
    from app import harness
    _SRC = (
        "import sys, json\n"
        "from OCP.TopoDS import TopoDS_Shape\n"
        "from OCP.StlAPI import StlAPI_Reader\n"
        "from OCP.GProp import GProp_GProps\n"
        "from OCP.BRepGProp import BRepGProp\n"
        "sh = TopoDS_Shape()\n"
        "try:\n"
        "    ok = StlAPI_Reader().Read(sh, sys.argv[1])\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok': False, 'vol': 0.0, 'err': f'read exception: {e}'}))\n"
        "    sys.exit(0)\n"
        "props = GProp_GProps()\n"
        "BRepGProp.VolumeProperties_s(sh, props)\n"
        "print(json.dumps({'ok': bool(ok), 'vol': props.Mass()}))\n")
    # 每次自检用独立脚本文件名, 避免并发任务写同一文件触发 Permission denied
    tmp_dir = AGENT_ROOT / "output" / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = None
    for _ in range(3):
        cand = tmp_dir / f"_volcheck_{_uuid.uuid4().hex[:8]}.py"
        try:
            cand.write_text(_SRC, "utf-8")
            script = cand
            break
        except Exception:
            continue
    if script is None:
        return {"ok": False, "vol": 0.0, "err": "自检脚本写入失败 (临时目录不可写)"}

    def _cleanup():
        try:
            script.unlink(missing_ok=True)
        except Exception:
            pass

    if not stl.exists() or stl.stat().st_size < 1000:
        _cleanup()
        return {"ok": False, "vol": 0.0, "err": "STL 缺失或过小"}
    try:
        r = subprocess.run([harness.PY, str(script), str(stl)],
                           capture_output=True, text=True, timeout=90,
                           env=harness.worker_env())
        info = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        vol = float(info.get("vol", 0.0))
        ok = bool(info.get("ok")) and vol > 1.0
        return {"ok": ok, "vol": vol,
                "err": None if ok else info.get("err") or f"体积退化 (vol={vol:.2f})"}
    except Exception as e:
        return {"ok": False, "vol": 0.0, "err": f"自检执行异常: {e}"}
    finally:
        _cleanup()


async def _heal_round(gw, vdir: Path, vnum: int, drawing_img: bytes,
                      best_code: str, reason: str, harness, progress) -> dict | None:
    """自愈轮: 把退化原因+上轮代码喂给 LLM 重写, 执行并返回 {score?, issues, code, stl_ok}."""
    ctx = (f"自检发现最终模型存在问题: {reason}\n"
           f"上一版代码:\n```python\n{best_code}\n```\n"
           f"请输出修复后的完整代码 (保证生成体积为正、拓扑有效的实体, 结尾必须写 STL 到 sys.argv[1])。")
    res = await gw.vision_json([drawing_img], FIX_SYSTEM, ctx)
    code = (res or {}).get("code")
    if not code:
        return None
    (vdir / "worker.py").write_text(code, "utf-8")
    stl = vdir / "model.stl"
    step_out = vdir / "model.step"
    r = harness.run_worker(code, stl, out_step=step_out)
    inner_repaired = 0
    while not r["ok"] and inner_repaired < INNER_REPAIR_MAX:
        inner_repaired += 1
        progress(f"[E] 自愈轮执行失败, 轮内重写 {inner_repaired}/{INNER_REPAIR_MAX}: {r['stderr'][:110]}")
        fix_ctx = (f"执行报错 (rc={r['rc']}):\n```\n{r['stderr'][-1200:]}\n```\n"
                   f"失败的代码:\n```python\n{code[-6000:]}\n```\n"
                   f"请完全自由地重写/修改/修复代码 (可更换建模思路), 输出 JSON。")
        fres = await gw.vision_json([drawing_img], INNER_FIX_SYSTEM, fix_ctx)
        new_code = (fres or {}).get("code")
        if not new_code:
            break
        code = new_code
        (vdir / f"worker_inner{inner_repaired}.py").write_text(code, "utf-8")
        r = harness.run_worker(code, stl, out_step=step_out)
    if not r["ok"]:
        progress(f"[E] 自愈轮执行失败(含 {inner_repaired} 次轮内重写): {r['stderr'][:120]}")
        return None
    if inner_repaired:
        (vdir / "worker.py").write_text(code, "utf-8")
    chk = stl_selfcheck(stl)
    if not chk["ok"]:
        progress(f"[E] 自愈轮模型仍退化: {chk['err']}")
        return None
    # 几何校验 (STEP 读回) 作为自愈轮的额外验收
    from app.cadcheck import check_step
    cchk = check_step(step_out)
    (vdir / "cadcheck.json").write_text(json.dumps(cchk, ensure_ascii=False), "utf-8")
    if not cchk.get("ok"):
        progress(f"[E] 自愈轮几何校验未通过: {'; '.join(cchk.get('issues', [])[:2])}")
        # 几何硬伤不阻断自愈轮交付 (视觉分仍可用), 但记录差异
    renders = _renders(stl, vdir / "renders")
    score, issues = None, []
    if renders:
        from app import convergence
        vres = await convergence.validate_round(
            [p.read_bytes() for p in renders], drawing_img)
        score, issues = vres.get("score"), vres.get("issues", [])
    (vdir / "compare.json").write_text(json.dumps(
        {"score": score, "issues": issues}, ensure_ascii=False), "utf-8")
    progress(f"[E] 自愈轮通过自检, score={score}")
    return {"code": code, "score": score, "issues": issues, "stl": stl}


async def run_evolve(pdf_path: Path, job_dir: Path, max_rounds: int = 4,
                     score_thr: float = 0.8, progress=print,
                     topology_detail: str = "", round_offset: int = 0) -> dict:
    """返回 {versions, best_round, best_score, elapsed, report_path}.
    收敛策略 (交付双线):
    ① 快速交付线: 分数 >= DELIVER_SCORE (0.9) 时即使有遗留问题也直接交付
       (几何硬伤除外——拓扑非法/体积退化/无实体必须继续修);
    ② 常规收敛线: 分数 >= score_thr 且无遗留问题;
    两条线都未满足时自动扩展迭代预算 (上限 HARD_MAX_ROUNDS),
    绝不因"到轮次上限"就带着低分草草交付.
    topology_detail 非空 = 拓扑升级模式: 首轮用特征清单硬约束生成代码.
    round_offset: 版本目录编号偏移 (从 pipeline 升级时接续编号, 避免覆盖 v1)."""
    from app import harness, convergence
    from llm.gateway import get_gateway, usage_reset, usage_since, usage_total
    gw = get_gateway()
    if not gw.online:
        raise RuntimeError("evolve 模式需要在线 LLM (设置 LLM_API_KEY)")
    # token 用量: 任务维度清零, 之后按轮取增量 (仅 evolve 直接启动时清零;
    # pipeline 升级进来的接续统计, 由 pipeline 侧统一初始化)
    if round_offset == 0 and not topology_detail:
        usage_reset()

    from app.pipeline import s1_preprocess
    job_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not (job_dir / "overview.png").exists():
        s1_preprocess(pdf_path, job_dir)
    # 解析前无损高清化: 扫描型原样提取内嵌母版 / 矢量型高倍率渲染; 失败回退旧总览
    drawing_img = (job_dir / "overview.png").read_bytes()
    try:
        from app.enhance import enhance_drawing
        meta = enhance_drawing(pdf_path, job_dir)
        ov_hd = meta.get("overview_hd")
        if ov_hd and Path(ov_hd).exists() and Path(ov_hd).stat().st_size > 2000:
            drawing_img = Path(ov_hd).read_bytes()
            progress(f"[E1] 图纸无损高清化: {meta.get('source')} "
                     f"{meta.get('w')}x{meta.get('h')}"
                     + (" (短边限幅)" if meta.get("limited") else " (原样提取)"))
        else:
            progress(f"[E1] 高清化无产物, 使用标准总览图")
    except Exception as e:
        progress(f"[E1] 高清化异常回退标准总览图: {e}")
    progress("[E1] 图纸预处理就绪")

    history = []
    best = {"round": 0, "score": -1.0}
    prev_code, prev_renders, prev_issues, prev_err = "", [], [], ""
    token_tot = {"prompt": 0, "completion": 0, "calls": 0}   # 任务级累计

    # 可扩展迭代预算: 基础预算用尽仍未收敛 (分数<阈值 或 有遗留问题) 时自动扩展,
    # 直到收敛或硬上限 HARD_MAX_ROUNDS (防无限迭代)
    budget = max(2, max_rounds)
    hard_cap = max(budget, HARD_MAX_ROUNDS)
    budget_extended = False
    converged = False
    rnd = 0
    while rnd < hard_cap:
        rnd += 1
        if rnd == budget + 1 and not budget_extended:
            budget_extended = True
            progress(f"[E] 基础预算 {budget} 轮用尽仍未收敛 "
                     f"(分数<{score_thr} 或有遗留问题), 自动扩展迭代预算至 {hard_cap} 轮")
        vnum = rnd + round_offset   # 实际版本目录号 (升级模式接续编号)
        vdir = job_dir / f"v{vnum}"; vdir.mkdir(parents=True, exist_ok=True)
        # ---- 生成/修复代码 ----
        if rnd == 1:
            if topology_detail:
                gen_prompt = ("按图纸生成 worker 代码, 输出 JSON。必须逐条落实特征清单:\n"
                              + topology_detail)
                res = await gw.vision_json([drawing_img], TOPO_GEN_SYSTEM, gen_prompt)
            else:
                res = await gw.vision_json([drawing_img], GEN_SYSTEM,
                                           "按图纸生成 worker 代码, 输出 JSON。")
        else:
            imgs_in = [drawing_img] + [p.read_bytes() for p in prev_renders[:4]]
            ctx = (f"上版代码:\n```python\n{prev_code}\n```\n"
                   f"执行输出: {prev_err[-800:] if prev_err else '(成功)'}\n"
                   f"视觉差异 (图纸为真值, 模型缺/错什么):\n"
                   + "\n".join(f"- {i.get('part','')}: {i.get('symptom','')}" for i in prev_issues[:8]))
            res = await gw.vision_json(imgs_in, FIX_SYSTEM, ctx + "\n输出修复后完整代码 JSON。")

        code = (res or {}).get("code")
        if not code:
            progress(f"[E] v{vnum} LLM 未返回代码, 终止"); break
        (vdir / "worker.py").write_text(code, "utf-8")
        (vdir / "notes.json").write_text(json.dumps(
            {"notes": (res or {}).get("notes", "")}, ensure_ascii=False), "utf-8")

        # ---- harness 执行 (失败 → 轮内即时重写, LLM 对代码有完全自由) ----
        progress(f"[E] v{vnum} harness 执行 worker...")
        stl = vdir / "model.stl"
        step_out = vdir / "model.step"
        r = harness.run_worker(code, stl, out_step=step_out)
        inner_repaired = 0
        while not r["ok"] and inner_repaired < INNER_REPAIR_MAX:
            inner_repaired += 1
            progress(f"[E] v{vnum} 执行失败, 轮内重写 {inner_repaired}/{INNER_REPAIR_MAX}: {r['stderr'][:110]}")
            fix_ctx = (f"执行报错 (rc={r['rc']}):\n```\n{r['stderr'][-1200:]}\n```\n"
                       f"失败的代码:\n```python\n{code[-6000:]}\n```\n"
                       f"请完全自由地重写/修改/修复代码 (可更换建模思路), 输出 JSON。")
            fres = await gw.vision_json([drawing_img], INNER_FIX_SYSTEM, fix_ctx)
            new_code = (fres or {}).get("code")
            if not new_code:
                progress(f"[E] v{vnum} 轮内重写未返回代码")
                break
            code = new_code
            (vdir / f"worker_inner{inner_repaired}.py").write_text(code, "utf-8")
            r = harness.run_worker(code, stl, out_step=step_out)
        if not r["ok"]:
            progress(f"[E] v{vnum} 执行失败(含 {inner_repaired} 次轮内重写): {r['stderr'][:150]}")
            u = usage_since()
            for k in token_tot:
                token_tot[k] += u[k]
            progress(f"[E] v{vnum} token 消耗: prompt={u['prompt']} completion={u['completion']} "
                     f"(调用 {u['calls']} 次)")
            history.append({"round": vnum, "stage": "exec_fail", "score": None,
                            "issues": [{"part": "exec", "symptom": r["stderr"][:300]}],
                            "code": code, "inner_repaired": inner_repaired})
            prev_code, prev_err, prev_issues, prev_renders = code, r["stderr"], \
                [{"part": "exec", "symptom": r["stderr"][:300]}], []
            continue
        if inner_repaired:
            progress(f"[E] v{vnum} 轮内重写 {inner_repaired} 次后执行成功")
            (vdir / "worker.py").write_text(code, "utf-8")  # 最终可运行版本覆盖主文件
        prev_err = ""

        # ---- cadquery 几何校验 (STEP 读回: 拓扑/体积/包围盒), 问题直接作差异项喂回 ----
        from app.cadcheck import check_step
        chk = check_step(step_out)
        (vdir / "cadcheck.json").write_text(json.dumps(chk, ensure_ascii=False), "utf-8")
        geo_issues = [{"part": "geometry", "symptom": s} for s in chk.get("issues", [])]
        if geo_issues:
            progress(f"[E] v{vnum} 几何校验未通过: {'; '.join(s['symptom'][:60] for s in geo_issues)}")
        else:
            progress(f"[E] v{vnum} 几何校验通过 "
                     f"(solids={chk.get('solids')}, vol={chk.get('vol')}, bbox={chk.get('bbox')})")

        # ---- 渲染 + 视觉比对 ----
        progress(f"[E] v{vnum} 渲染+视觉比对...")
        renders = _renders(stl, vdir / "renders")
        if renders:
            vres = await convergence.validate_round(
                [p.read_bytes() for p in renders], drawing_img)
            score = vres.get("score"); issues = vres.get("issues", [])
        else:
            score, issues = None, []
        # 几何校验问题并入遗留问题: 有几何硬伤时即使视觉分达标也不允许收敛
        issues = geo_issues + issues
        (vdir / "compare.json").write_text(json.dumps(
            {"score": score, "issues": issues,
             "cadcheck": {"ok": chk.get("ok"), "vol": chk.get("vol"),
                          "solids": chk.get("solids"), "bbox": chk.get("bbox")}},
            ensure_ascii=False), "utf-8")
        history.append({"round": vnum, "stage": "ok", "score": score,
                        "issues": issues, "code": code})
        progress(f"[E] v{vnum} score={score} issues={len(issues)}")
        # ---- 本轮 token 消耗 ----
        u = usage_since()
        for k in token_tot:
            token_tot[k] += u[k]
        progress(f"[E] v{vnum} token 消耗: prompt={u['prompt']} completion={u['completion']} "
                 f"(调用 {u['calls']} 次)")

        if (score or 0) > best["score"]:
            best = {"round": vnum, "score": score or 0.0}
        # 收敛判定 (交付策略):
        #  ① 快速交付线: 分数 ≥ DELIVER_SCORE (0.9) 时即使有遗留问题也直接交付
        #     (视觉比对已高度一致, 剩余多为次要差异); 几何硬伤除外——拓扑非法/
        #     体积退化/无实体时没有可交付实体, 必须继续修复。
        #     (geo_blocked 只认 cadcheck 真实解析模型后发现的硬伤——err 为 None;
        #      STEP 缺失/超时等校验基建问题不阻断快速线, 交付前自测的 STL
        #      体积校验仍会兜底。)
        #  ② 常规收敛线: 分数 ≥ score_thr 且无遗留问题。
        geo_blocked = (chk.get("err") is None) and not chk.get("ok", False)
        if score is not None and score >= DELIVER_SCORE and not geo_blocked:
            if issues:
                progress(f"[E] v{vnum} 分数 {score:.2f}≥{DELIVER_SCORE} 达到快速交付线, "
                         f"携 {len(issues)} 项遗留问题交付")
            else:
                progress(f"[E] v{vnum} 达到收敛标准 (分数 {score:.2f}≥{DELIVER_SCORE} 且无遗留问题), 停止")
            converged = True
            break
        if score is not None and score >= score_thr and not issues:
            progress(f"[E] v{vnum} 达到收敛标准 (分数 {score:.2f}≥{score_thr} 且无遗留问题), 停止")
            converged = True
            break
        if score is not None and score >= score_thr:
            progress(f"[E] v{vnum} 分数达标 ({score:.2f}) 但仍有 {len(issues)} 项遗留问题, 继续迭代消除")
        prev_code, prev_issues, prev_renders = code, issues, renders

    if not converged:
        progress(f"[E] 达到硬上限 {hard_cap} 轮仍未完全收敛, 按最优版本交付 "
                 f"(best v{best['round']} score={best['score']:.2f})")

    # ---- 交付前自测自愈 (OCP 体积自检 + cadquery 几何校验双保险) ----
    best_round = best["round"]
    if best_round:
        bstl = job_dir / f"v{best_round}" / "model.stl"
        bstep = job_dir / f"v{best_round}" / "model.step"
        progress(f"[E] 交付前自测 (体积/拓扑 + cadquery 几何校验)...")
        chk = stl_selfcheck(bstl)
        if chk["ok"] and bstep.exists():
            # STL 自检通过再做 STEP 几何校验, 拓扑非法同样触发自愈
            from app.cadcheck import check_step
            cchk = check_step(bstep)
            if not cchk.get("ok"):
                chk = {"ok": False, "vol": chk.get("vol", 0.0),
                       "err": "cadcheck: " + "; ".join(cchk.get("issues", [])[:2])}
                progress(f"[E] cadquery 几何校验发现问题: {chk['err'][:120]}")
        if not chk["ok"]:
            progress(f"[E] 自测失败: 最优 v{best_round} 模型退化 ({chk['err']}), 启动自愈轮...")
            hnum = round_offset + max_rounds + 1
            hvdir = job_dir / f"v{hnum}"; hvdir.mkdir(parents=True, exist_ok=True)
            try:
                best_code = (job_dir / f"v{best_round}" / "worker.py").read_text("utf-8")
            except Exception:
                best_code = prev_code
            hr = await _heal_round(gw, hvdir, hnum, drawing_img, best_code,
                                   chk["err"], harness, progress)
            if hr:
                history.append({"round": hnum, "stage": "ok", "score": hr["score"],
                                "issues": hr["issues"], "code": hr["code"], "heal": True})
                best = {"round": hnum, "score": hr["score"] or 0.0}
                progress(f"[E] 自愈成功, 交付 v{hnum} (score={hr['score']})")
            else:
                # 自愈失败: 在其余成功轮中按分数降序找一个通过自检的版本
                progress(f"[E] 自愈失败, 回退寻找可交付版本...")
                for h in sorted([x for x in history
                                 if x.get("stage") == "ok" and x["round"] != best_round],
                                key=lambda x: x.get("score") or 0, reverse=True):
                    if stl_selfcheck(job_dir / f"v{h['round']}" / "model.stl")["ok"]:
                        best = {"round": h["round"], "score": h.get("score") or 0.0}
                        progress(f"[E] 回退选定 v{h['round']} 交付")
                        break
                else:
                    progress("[E] 无版本通过自检, 仍按最优轮交付并标注")
        # ---- GLB 生成 + 验证 (失败重试一次) ----
        bstl = job_dir / f"v{best['round']}" / "model.stl"
        bglb = job_dir / f"v{best['round']}" / "model.glb"
        if bstl.exists():
            _glb(bstl, bglb)
            if not glb_ok(bglb):
                progress("[E] GLB 验证失败, 重新生成...")
                bglb.unlink(missing_ok=True)
                _glb(bstl, bglb)
        best_round = best["round"]
    # ---- 任务 token 总量 (循环累计 + 自愈/产物阶段残余) ----
    tail = usage_total()
    for k in token_tot:
        token_tot[k] += tail[k]
    progress(f"[E] 任务总 token 消耗: prompt={token_tot['prompt']} "
             f"completion={token_tot['completion']} (共调用 {token_tot['calls']} 次)")
    from app.report import render_report
    spec = {"part_type": "evolve", "_source": "llm_code",
            "best_round": best_round, "best_score": best["score"],
            "token_usage": token_tot}
    # ---- 统一产物生成 (STEP/OBJ/DXF/三视图投影亮暗版/G-Code) ----
    from app.artifacts import finalize
    bstl_final = (job_dir / f"v{best_round}" / "model.stl") if best_round else None
    if bstl_final and bstl_final.exists():
        try:
            finalize(job_dir, bstl_final, family="", spec=spec, progress=progress)
        except Exception as e:
            progress(f"[F] 统一产物生成异常(不影响主交付): {e}")
    report = render_report(job_dir, spec, history, "evolve", elapsed_sec=time.time() - t0)
    return {"versions": len(history), "best_round": best_round,
            "best_score": best["score"], "elapsed": time.time() - t0,
            "report_path": report, "history": history}
