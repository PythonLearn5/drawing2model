# -*- coding: utf-8 -*-
"""
harness — LLM 生成 worker 代码的沙箱执行器
设计 (对齐 StaffDeck bubblewrap harness 思路, 本地 Windows 版):
- 静态危险模式扫描 (os.system/subprocess/socket/eval/exec/网络/环境变量读取等) -> 拒绝执行
- 受限子进程: 仅注入 OCP 环境 PYTHONPATH, 剥离 LLM Key 等敏感 env, 隔离 cwd, 硬超时
- 只允许 worker 往 argv[1] (调用方指定的 job 目录内) 写 STL
worker 契约: python worker.py <out.stl>, 自包含, 只用 OCP/math/json/sys
"""
from __future__ import annotations
import re, os, subprocess, tempfile
from pathlib import Path

# 跨平台运行时路径 (环境变量可覆盖, 见 app/runtime.py; Windows 默认=开发机托管 python)
from app.runtime import WORKER_PY as PY, OCP_PYTHONPATH as OCP_ENV, worker_env, home_dir

# 自动注入的通用 import 头 (LLM 生成代码常漏 import, 统一兜底;
# cadquery/numpy 为推荐建模栈, try 包裹保证缺失时不阻断纯 OCP 代码;
# star import 的符号均带 OCP 类名前缀, 与 worker 自身 import 不冲突)
IMPORT_HEADER = """import sys, math, json
try:
    import numpy as np
except Exception:
    np = None
try:
    import cadquery as cq
except Exception:
    cq = None
from OCP.gp import *
from OCP.BRepPrimAPI import *
from OCP.BRepAlgoAPI import *
from OCP.BRepBuilderAPI import *
from OCP.BRepOffsetAPI import *
from OCP.BRepFilletAPI import *
from OCP.BRepMesh import *
from OCP.StlAPI import *
from OCP.GProp import *
from OCP.BRepGProp import *
from OCP.GeomAPI import *
from OCP.TColgp import *
from OCP.TopExp import *
from OCP.TopAbs import *
from OCP.TopoDS import *
"""

# 危险模式 (出现即拒绝执行)
# 原则: 只拦截真正危险的能力 (系统调用/网络/进程/动态执行),
# 不限制建模手段——open()、常规标准库均放开, 只要能产出正确结构的模型各种方法都可用.
DANGEROUS_PATTERNS = [
    r"\bos\s*\.\s*(system|popen|exec[lv]?[ep]?\s*\(|spawn|remove|unlink|rmdir|rename|replace|chdir|makedirs|mkdir|environ|getenv)",
    r"\bshutil\s*\.\s*(rmtree|move|copy)",
    r"\bsubprocess\b",
    r"\bsocket\b", r"\burllib\b", r"\brequests\b", r"\bhttp\.client\b",
    r"\bftplib\b", r"\btelnetlib\b", r"\bsmtplib\b", r"\bwebbrowser\b",
    r"\beval\s*\(", r"\bexec\s*\(", r"__import__\s*\(", r"\bimportlib\b",
    r"\bctypes\b", r"\bmultiprocessing\b",
]


def scan_code(code: str) -> list[str]:
    """返回命中的危险模式列表; 空列表 = 通过."""
    hits = []
    for pat in DANGEROUS_PATTERNS:
        m = re.search(pat, code)
        if m:
            hits.append(f"{pat} 命中: {m.group(0)}")
    return hits


def fix_indent(code: str, max_fix: int = 8) -> tuple[str, int]:
    """LLM 全量重写代码时偶发行首缩进污染 (多/少空格) 导致 IndentationError,
    逐行对齐修复 (与上一条非空语句同缩进), 最多修 max_fix 处.
    返回 (修复后代码, 修复次数)."""
    fixed = 0
    for _ in range(max_fix):
        try:
            compile(code, "<worker>", "exec")
            return code, fixed
        except IndentationError as e:
            lines = code.split("\n")
            i = (e.lineno or 1) - 1
            if not (0 <= i < len(lines)):
                break
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            indent = (len(lines[j]) - len(lines[j].lstrip(" "))) if j >= 0 else 0
            lines[i] = " " * indent + lines[i].lstrip(" ")
            code = "\n".join(lines)
            fixed += 1
        except SyntaxError:
            break
    return code, fixed


def run_worker(code: str, out_stl: Path, timeout: int = 240,
               code_path: Path | None = None, out_step: Path | None = None) -> dict:
    """在受限子进程中执行 worker 代码.
    out_step 非空时作为 argv[2] 传入, 供 worker 用 cadquery 导出 STEP (几何校验用).
    返回 {ok, rc, stdout, stderr, stl_exists}."""
    out_stl = Path(out_stl).resolve()
    out_stl.parent.mkdir(parents=True, exist_ok=True)
    if code_path is None:
        code_path = out_stl.parent / "worker.py"
    code_path = Path(code_path).resolve()
    # 缩进自愈: LLM 修复轮偶发行首缩进污染 (IndentationError), 逐行对齐修复后再执行
    code, indent_fixes = fix_indent(code)
    # 注入通用 import 头 (只对 worker 代码做安全扫描, 头部是受信代码)
    full = IMPORT_HEADER + "\n" + code if not code.startswith(IMPORT_HEADER[:40]) else code
    code_path.write_text(full, "utf-8")

    hits = scan_code(code)
    if hits:
        return {"ok": False, "rc": -1, "stdout": "", "stl_exists": False,
                "indent_fixes": indent_fixes,
                "stderr": "静态安全扫描拒绝: " + "; ".join(hits[:5])}

    # 语法预检: 缩进自愈后仍编译不过, 直接返回精确报错 (不启动子进程)
    try:
        compile(full, "<worker>", "exec")
    except SyntaxError as e:
        return {"ok": False, "rc": -4, "stdout": "", "stl_exists": False,
                "indent_fixes": indent_fixes,
                "stderr": f"语法错误 (line {e.lineno}): {e.msg}" +
                          (f"; 已自动修复 {indent_fixes} 处缩进仍未通过" if indent_fixes else "")}

    # 隔离 env: 只留最小必要, 剥离所有凭证
    # HOME/USERPROFILE 必给: cadquery 导入时用 Path("~").expanduser() 找资源,
    # 缺失会直接 RuntimeError (无法建模); 跨平台细节见 app/runtime.py
    safe_env = worker_env({
        "TEMP": os.environ.get("TEMP", tempfile.gettempdir()),
        "TMP": os.environ.get("TMP", tempfile.gettempdir()),
    })
    cmd = [PY, str(code_path), str(out_stl)]
    if out_step is not None:
        cmd.append(str(Path(out_step).resolve()))
    try:
        r = subprocess.run(cmd,
                           capture_output=True, text=True, timeout=timeout,
                           env=safe_env, cwd=str(out_stl.parent))
        # OCP/cadquery 已知行为: 解释器退出清理段偶发段错误 (rc=3221226356/139),
        # 此时 STL/STEP 产物均已完整写出, 不应判失败; 产物缺失才算失败
        # (真实报错场景: 语法/运行时异常 -> stderr 非空且无产物).
        crashed_exit = r.returncode not in (0,) and out_stl.exists() and r.stderr.strip() == ""
        return {"ok": (r.returncode == 0 or crashed_exit) and out_stl.exists(),
                "rc": r.returncode, "stdout": r.stdout[-2000:],
                "stderr": r.stderr[-2000:], "stl_exists": out_stl.exists(),
                "indent_fixes": indent_fixes,
                "exit_crash": crashed_exit}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -2, "stdout": "", "stl_exists": False,
                "stderr": f"执行超时 (>{timeout}s)"}
    except Exception as e:
        return {"ok": False, "rc": -3, "stdout": "", "stl_exists": False,
                "stderr": f"执行异常: {e}"}
