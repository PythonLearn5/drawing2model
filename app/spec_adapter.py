# -*- coding: utf-8 -*-
"""
VLM spec → PartSpec schema 适配器
Kimi/Qwen 输出语义化 features 列表, box_housing worker 期望 bores/cavities/flange 数组.
本模块把前者的语义描述转换为后者的工程化结构, 并补默认参数.

VLM 输出 (semantic):
{
  "part_type": "box_housing",
  "outer": {"L": 455, "W": 300, "H": 200, "slope_deg": 15},  // 可能缺字段
  "features": [
    {"type": "stepped_bore", "axis": "X",
     "segments": [{"d": 230, "len": 134}, {"d": 215, "len": 60}, {"d": 200, "len": 261}]},
    {"type": "flange", "d": 260, "t": 15,
     "holes": {"n": 8, "pcd": 220, "d": 6.8, "depth": 16}},
    {"type": "cavity_grid", "nx": 2, "ny": 2, "w": 190, "h": 215, "r": 10},
    {"type": "slope", "deg": 15, "h": 140},
    {"type": "hole_pattern", "face": "top", "n": 3, "d": 10.2, "depth": 24, "x": [...]},
    {"type": "hole_pattern", "face": "bottom", ...}
  ]
}

PartSpec (engineering, box_housing worker):
{
  "part_type": "box_housing",
  "outer": {"L":455, "W":435, "H":455, "slope_deg":15, "slope_h":140},
  "bores": [{"x":..., "y":0, "z":250, "d":..., "len":...}, ...],
  "flange": {"x":-L/2, "y":0, "z":250, "d":..., "t":..., "holes":{...}},
  "cavities": [{"cx":..., "cy":..., "w":..., "d":..., "h":..., "r":...}, ...],
  "top_holes": [{"x":..., "y":0, "d":..., "depth":...}, ...],
  "bottom_holes": [{"x":..., "y":0, "d":..., "depth":...}, ...]
}
"""
from __future__ import annotations
import json, copy, math
from typing import Any


# 默认参数 (图纸无标注时用)
DEFAULTS = {
    "Z_AXIS": 250.0,           # 主轴中心高
    "FLANGE_X": None,          # = -L/2 (运行时按 outer.L 计算)
    "SLOPE_H_RATIO": 0.30,     # 斜面高度占 H 的比例
    "CENTER_HOLE_R": 5.1,      # M12 底孔半径
    "CAV_R": 10.0,
}


def vlm_to_partspec(vlm: dict) -> dict:
    """主入口: VLM spec -> PartSpec (box_housing worker 格式)"""
    if not isinstance(vlm, dict):
        return {}
    out = {"part_type": vlm.get("part_type") or vlm.get("family") or "box_housing",
           "outer": _norm_outer(vlm.get("outer", {})),
           "bores": [],
           "flange": None,
           "cavities": [],
           "top_holes": [],
           "bottom_holes": [],
           "uncertain": list(vlm.get("uncertain") or []),
           "_source": "vlm+adapted"}

    # ---- 新零件族透传: roller/shaft/blade 直接携带 VLM 原生字段 ----
    if out["part_type"] in ("roller", "shaft", "blade"):
        for k in ("L", "body_L", "major_d", "minor_d", "lead", "pitch",
                  "groove_depth", "journal", "segments", "bores", "grooves",
                  "span", "chord", "sections", "root", "twist_deg"):
            if k in vlm:
                out[k] = vlm[k]
        # outer.L 兜底为零件总长
        if not out["outer"].get("L") or out["outer"]["L"] <= 0:
            out["outer"]["L"] = float(out.get("L") or out.get("span") or 100)
        return out

    L = out["outer"]["L"]; Z = DEFAULTS["Z_AXIS"]
    fl_x = DEFAULTS["FLANGE_X"] if DEFAULTS["FLANGE_X"] is not None else -L / 2

    # 解析 features 列表
    for f in vlm.get("features", []):
        ftype = (f.get("type") or "").lower().strip()
        if ftype in ("stepped_bore", "bore", "孔系"):
            out["bores"] += _split_bore(f.get("segments") or _bore_from_feature(f), fl_x, Z)
        elif ftype in ("flange", "凸缘"):
            out["flange"] = _norm_flange(f, fl_x, Z)
        elif ftype in ("cavity_grid", "减重腔", "cavity", "腔体"):
            out["cavities"] += _norm_cavities(f, L)
        elif ftype in ("slope", "斜面"):
            sd = f.get("deg", f.get("slope_deg", 15))
            sh = f.get("h", f.get("slope_h", out["outer"]["H"] * DEFAULTS["SLOPE_H_RATIO"]))
            out["outer"]["slope_deg"] = sd
            out["outer"]["slope_h"] = sh
        elif ftype in ("hole_pattern", "孔阵列", "lifting_hole", "mounting_hole"):
            _append_holes(out, f, L)
        else:
            out["uncertain"].append(f"未识别特征类型: {ftype} ({f})")

    # 兜底: 无 cavities 时用 2x2 网格默认值
    if not out["cavities"] and vlm.get("part_type") in ("box_housing", "shaft", "disc", "blade"):
        # 不强行填默认值, 让 VLM/案例库/cavity_grid 决定
        pass

    # 兜底: 无 flange 但有 bore 时, 加一个默认小法兰 (常见)
    return out


# ------------------- 各字段归一化 -------------------
def _norm_outer(o: dict) -> dict:
    """outer 字段: 必有 L/W/H; slope_deg/slope_h 可选 (默认 15°/0.3H)"""
    L = float(o.get("L", o.get("length") or 455))
    W = float(o.get("W", o.get("width") or 435))
    H = float(o.get("H", o.get("height") or 455))
    return {
        "L": L, "W": W, "H": H,
        "slope_deg": float(o.get("slope_deg", o.get("deg", 15))),
        "slope_h": float(o.get("slope_h", o.get("h", H * DEFAULTS["SLOPE_H_RATIO"]))),
    }


def _split_bore(segments: list, fl_x: float, Z: float) -> list[dict]:
    """把 stepped_bore 的 segments 列表展开为 worker 需要的 bores 数组.
    segments[0] 起点 x = fl_x (前法兰面), 后续串联. 若 x 字段缺失则按 len 累加."""
    if not segments:
        return []
    out = []
    cursor = fl_x
    for i, seg in enumerate(segments):
        d = float(seg.get("d", 0))
        ln = float(seg.get("len", seg.get("length") or 0))
        if d <= 0 or ln <= 0:
            continue
        out.append({"x": round(cursor, 3), "y": 0, "z": float(seg.get("z", Z)),
                    "d": d, "len": ln})
        cursor += ln
    return out


def _bore_from_feature(f: dict) -> list:
    """如果 VLM 把 bore 写成单对象而非 segments, 包装成 list"""
    d = f.get("d"); ln = f.get("len") or f.get("length")
    if d and ln:
        return [{"d": d, "len": ln}]
    return []


def _norm_flange(f: dict, fl_x: float, Z: float) -> dict:
    """flange: 必有 d/t, holes 可选"""
    d = float(f.get("d") or f.get("diameter") or 0)
    t = float(f.get("t") or f.get("thickness") or 15)
    if d <= 0:
        return None
    holes = f.get("holes") or {}
    norm_holes = {}
    if isinstance(holes, dict):
        norm_holes = {
            "n": int(holes.get("n", holes.get("count") or 0)),
            "pcd": float(holes.get("pcd", holes.get("PCD") or 0)),
            "d": float(holes.get("d", holes.get("size") or 6.8)),
            "depth": float(holes.get("depth", holes.get("dp") or 16)),
        }
    return {"x": fl_x, "y": 0, "z": Z, "d": d, "t": t, "holes": norm_holes}


def _norm_cavities(f: dict, L: float) -> list[dict]:
    """cavity_grid: nx, ny, w, h, r -> cavities[] 列表
    支持单 cavity 写法 (w, h, r 单值) 或 nx/ny 网格"""
    nx = int(f.get("nx", f.get("count_x", 1)))
    ny = int(f.get("ny", f.get("count_y", 1)))
    w = float(f.get("w", f.get("width") or 150))
    h = float(f.get("h", f.get("depth") or 200))   # 这里是高度(向上挖)
    r = float(f.get("r", f.get("r_corner") or DEFAULTS["CAV_R"]))
    # 单 cavity 也支持: 直接一项
    if nx == 1 and ny == 1 and f.get("cx") is not None:
        return [{"cx": float(f["cx"]), "cy": float(f.get("cy", 0)),
                 "w": w, "d": float(f.get("d", w)), "h": h, "r": r}]
    # 网格: 用 pitch 估算
    px = float(f.get("pitch_x", f.get("pitch", w * 1.2)))
    py = float(f.get("pitch_y", py if (py := f.get("pitch_y", w * 1.2)) else w * 1.2))
    out = []
    for ix in range(nx):
        for iy in range(ny):
            cx = (ix - (nx - 1) / 2) * px
            cy = (iy - (ny - 1) / 2) * py
            out.append({"cx": round(cx, 3), "cy": round(cy, 3),
                        "w": w, "d": w, "h": h, "r": r})
    return out


def _append_holes(out: dict, f: dict, L: float) -> None:
    """hole_pattern -> top_holes/bottom_holes
    启发式: face=top 或 z>0 视为顶面孔, 其余底面孔"""
    face = (f.get("face") or "").lower()
    n = int(f.get("n", f.get("count") or 0))
    d = float(f.get("d", f.get("size") or 0))
    depth = float(f.get("depth", f.get("dp") or 20))
    xs = f.get("x", f.get("xs"))  # 支持单值或列表
    if isinstance(xs, (int, float)):
        xs = [float(xs)]
    elif not xs:
        xs = [L / 2 - 100] * n if n else []
    if n > len(xs):
        xs = (xs * (n // len(xs) + 1))[:n]
    target = out["top_holes"] if (face == "top" or "M12" in (f.get("note") or "")
                                   or d >= 8) and face != "bottom" else out["bottom_holes"]
    for x in xs[:n]:
        target.append({"x": round(float(x), 3), "y": 0, "d": d, "depth": depth})


# ------------------- Schema 校验 -------------------
def validate_partspec(spec: dict) -> list[str]:
    """返回 warnings 列表: 必填字段缺失/值异常"""
    ws = []
    if not spec.get("part_type"):
        ws.append("part_type 缺失")
    o = spec.get("outer", {})
    for k in ("L", "W", "H"):
        v = o.get(k)
        if not v or v <= 0:
            ws.append(f"outer.{k}={v} 无效")
    if not spec.get("bores"):
        ws.append("bores 为空 (将无主轴孔)")
    if not spec.get("cavities"):
        ws.append("cavities 为空 (将无减重腔, 重量偏大)")
    return ws


# ------------------- 单测 -------------------
if __name__ == "__main__":
    sample = {
        "part_type": "box_housing",
        "outer": {"L": 455, "W": 435, "H": 455, "slope_deg": 15},
        "features": [
            {"type": "stepped_bore", "axis": "X",
             "segments": [{"d": 230, "len": 134}, {"d": 215, "len": 60}, {"d": 200, "len": 261}]},
            {"type": "flange", "d": 260, "t": 15,
             "holes": {"n": 8, "pcd": 220, "d": 6.8, "depth": 16}},
            {"type": "cavity_grid", "nx": 2, "ny": 2, "w": 190, "h": 215, "r": 10},
            {"type": "hole_pattern", "face": "top", "n": 3, "d": 10.2, "depth": 24, "x": 100},
        ],
        "uncertain": ["PT1/4 油孔角度未确认"]
    }
    out = vlm_to_partspec(sample)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("\nwarnings:", validate_partspec(out))
