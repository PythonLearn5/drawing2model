# -*- coding: utf-8 -*-
"""
runtime — 运行时解释器/环境路径的统一解析 (跨平台)

三个解释器角色:
- WORKER_PY : 沙箱执行 LLM 生成 worker 代码的解释器 (需 OCP/cadquery)
- UTIL_PY   : 跑工具脚本 (stl2glb/render_views/step_export/convert) 的解释器
- OCP_PYTHONPATH : worker 子进程注入的 PYTHONPATH (Windows 上为 --target 目录;
  Linux 上 worker venv 自带包, 留空即可)

环境变量覆盖 (部署时用, 优先级最高):
- D2M_WORKER_PY / D2M_UTIL_PY / D2M_OCP_PYTHONPATH

Windows 默认值: 开发机的 workbuddy 托管 python (保持现有行为不变).
Linux 默认值: 当前解释器自身 (部署脚本用两个 venv: server 与 worker).
"""
from __future__ import annotations
import os, sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# ---- Windows 默认 (开发机托管 python, 与历史行为一致) ----
_WIN_WORKER_PY = r"C:/Users/zkk/.workbuddy/binaries/python/versions/3.13.12/python.exe"
_WIN_OCP_PYTHONPATH = r"C:/Users/zkk/.workbuddy/binaries/python/envs/ocp_env"
_WIN_UTIL_PY = r"C:/Users/zkk/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

if IS_WINDOWS:
    WORKER_PY = os.environ.get("D2M_WORKER_PY") or _WIN_WORKER_PY
    OCP_PYTHONPATH = os.environ.get("D2M_OCP_PYTHONPATH") or _WIN_OCP_PYTHONPATH
    UTIL_PY = os.environ.get("D2M_UTIL_PY") or _WIN_UTIL_PY
else:
    # Linux: 默认用服务自身解释器; 部署时 systemd 注入 D2M_WORKER_PY 指向 worker venv
    WORKER_PY = os.environ.get("D2M_WORKER_PY") or sys.executable
    OCP_PYTHONPATH = os.environ.get("D2M_OCP_PYTHONPATH", "")
    UTIL_PY = os.environ.get("D2M_UTIL_PY") or sys.executable


def home_dir() -> str:
    """子进程需要的 HOME (cadquery 导入时 Path('~').expanduser() 依赖)."""
    return (os.environ.get("USERPROFILE") or os.environ.get("HOME")
            or str(Path.home()))


def worker_env(extra: dict | None = None) -> dict:
    """worker/OCP 子进程的最小隔离环境: 剥离凭证, 保留必要系统变量.
    Windows 需要 SYSTEMROOT/TEMP/TMP; Linux 只需 HOME/PATH(/TMPDIR)."""
    env = {"PATH": os.environ.get("PATH", ""),
           "HOME": home_dir()}
    if OCP_PYTHONPATH:
        env["PYTHONPATH"] = OCP_PYTHONPATH
    if IS_WINDOWS:
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["USERPROFILE"] = home_dir()
        env["TEMP"] = os.environ.get("TEMP", r"C:\Windows\Temp")
        env["TMP"] = os.environ.get("TMP", env["TEMP"])
    else:
        env["TMPDIR"] = os.environ.get("TMPDIR", "/tmp")
        env["LANG"] = os.environ.get("LANG", "C.UTF-8")
    if extra:
        env.update(extra)
    return env
