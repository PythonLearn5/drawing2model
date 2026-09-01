# -*- coding: utf-8 -*-
"""
CNC G-Code 生成器 — 按零件族生成最终版加工代码 (带中文注释)
输出: {mill/turn: {name, content, lines, equipment, tools}}
equipment/tools 同时供报告"设备与工装"章节使用。
CLI: python gcode.py <in.stl> <out_dir> [--family roller]
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
from datetime import datetime

from stl import mesh as stlmesh


def _bbox_mm(stl_path: Path) -> tuple:
    m = stlmesh.Mesh.from_file(str(stl_path))
    mn = m.vectors.reshape(-1, 3).min(0); mx = m.vectors.reshape(-1, 3).max(0)
    return mn, mx, (mx - mn)


def _header(comment_lines: list) -> str:
    """FANUC 风格注释头 (% ... %)"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    out = ["%", f"(DRAWING2MODEL 自动生成 G-CODE · {ts})", "(本代码为工程参考, 首件加工前请在 CAM 仿真环境中验证刀路)", "%"]
    out += [f"({c})" for c in comment_lines]
    return "\n".join(out)


def generate_turning(spec: dict, size) -> dict:
    """回转体 (滚柱/轴): 两轴车削."""
    L = max(size[0], size[2])  # 轴向长度
    D = max(size[0], size[1])   # 最大外径近似
    machine = "CJK6132 数控车床 (FANUC 0i-TF)"
    fixture = "三爪液压卡盘夹持左端, 悬伸不超过 3 倍直径; 精加工用活顶尖顶持右端"
    tools = [
        ("T01", "WNMG080408 外圆粗车刀片", "主轴 800 rpm / F0.25 mm/r / ap 2.0 mm"),
        ("T02", "TNMG110304 外圆精车刀片", "主轴 1500 rpm / F0.08 mm/r / ap 0.2 mm"),
        ("T03", "φ3 中心钻", "主轴 2000 rpm / F0.05"),
        ("T04", "切槽刀 宽 3 mm", "主轴 600 rpm / F0.06"),
    ]
    lines = []
    lines += _header([
        f"零件族: 回转体(车削)  总长 {L:.1f} mm  最大外径 φ{D:.1f}",
        "工艺: 粗车外圆 -> 精车外圆 -> 切槽/倒角 -> 切断",
        f"设备: {machine}",
    ]).split("\n")
    lines += [
        "O1001 (Turning Main Program)",
        "G21 G17 G40 G90 G94 (毫米制/取消补偿)",
        "G28 U0 W0 (回参考点)",
        "T0101 (换粗车刀)",
        "M03 S800 (主轴正转 800)",
        "G00 X%r Z2.0 (快速定位至毛坯外圆)" % (D + 4),
        "G71 U2.0 R0.5 (外圆粗车循环: 切深2 退刀0.5)",
        "G71 P100 Q200 U0.5 W0.2 F0.25 (粗车留余量 0.5)",
        "N100 G00 X0",
        f"G01 Z0 F0.25 (端面)",
        f"G01 X{D * 0.9:.1f} (外圆段)",
        f"G01 Z-{L * 0.95:.1f} (车至接近总长)",
        "N200 G01 X%r" % (D + 4),
        "T0202 (换精车刀)",
        "M03 S1500",
        "G70 P100 Q200 F0.08 (精车循环)",
        "T0404 (切槽刀)",
        "M03 S600",
        "G00 X%r Z-2.0" % (D + 2),
        "G01 X0.5 F0.06 (端面倒角/切槽)",
        "G00 X%r" % (D + 6),
        "M05 (主轴停)",
        "M30 (程序结束并复位)",
    ]
    content = "\n".join(lines)
    return {"name": "model_turn.nc", "content": content, "lines": len(lines),
            "equipment": machine, "fixture": fixture, "tools": tools}


def generate_milling(spec: dict, size, family: str) -> dict:
    """铣削类 (箱壳 2.5D / 叶片多轴简化为 3 轴)."""
    five_axis = family == "blade"
    machine = ("DMG MORI DMU 50 五轴加工中心 (Heidenhain TNC640)" if five_axis
               else "VMC850 立式加工中心 (FANUC 0i-MF)")
    fixture = ("专用叶片夹具 + 真空吸盘定位, 找正基准面平面度≤0.02" if five_axis
               else "平口钳装夹, 垫平行块, 找正基准角")
    tools = [
        ("T01", "φ16 硬质合金立铣刀 (粗铣)", "主轴 3500 rpm / F1200 mm/min / ap 3 mm"),
        ("T02", "φ10 硬质合金立铣刀 (精铣)", "主轴 6000 rpm / F800 mm/min / ap 0.5 mm"),
        ("T03", "φ6 球头铣刀 (型面)", "主轴 8000 rpm / F600 mm/min"),
        ("T04", "φ8.5 麻花钻 (孔加工)", "主轴 2500 rpm / F200 mm/min"),
    ]
    X, Y, Z = size
    lines = _header([
        f"零件族: {family}  外形 {X:.1f} x {Y:.1f} x {Z:.1f} mm",
        "工艺: 面铣/型腔粗铣 -> 轮廓精铣 -> 孔系 -> 型面(球头)",
        f"设备: {machine}",
    ]).split("\n")
    lines += [
        "O2001 (Milling Main Program)",
        "G21 G17 G40 G49 G80 G90 (安全初始化)",
        "G54 (工件坐标系)",
        "G00 Z100.0 (抬刀至安全高度)",
        "T01 M06 (换粗铣刀)",
        "M03 S3500 (主轴启动)",
        f"G00 X-{X/2 + 5:.1f} Y-{Y/2 + 5:.1f} (定位至毛坯角外)",
        "G01 Z-3.0 F500 (下刀至首层切深)",
        f"G01 X{X/2 + 5:.1f} F1200 (面铣走刀 1)",
        f"Y-{Y/2 + 3:.1f}",
        f"G01 X-{X/2 + 5:.1f} (面铣走刀 2)",
        "G00 Z50.0",
        "T02 M06 (换精铣刀)",
        "M03 S6000",
        f"G00 X-{X/2:.1f} Y-{Y/2:.1f}",
        f"G01 Z-{Z * 0.98:.1f} F300 (侧壁精铣下刀)",
        f"G01 X{X/2:.1f} F800 (轮廓精铣)",
        f"Y{Y/2:.1f}",
        f"X-{X/2:.1f}",
        f"Y-{Y/2:.1f}",
        "G00 Z100.0",
        "T04 M06 (换钻头)",
        "M03 S2500",
        "G81 X0 Y0 Z-%.1f R5.0 F200 (中心孔钻削循环)" % (Z * 0.6),
        "G80 (取消钻孔循环)",
        "M05",
        "M30 (程序结束)",
    ]
    content = "\n".join(lines)
    return {"name": "model_mill.nc", "content": content, "lines": len(lines),
            "equipment": machine, "fixture": fixture, "tools": tools}


def generate(stl_path: Path, family: str, spec: dict) -> dict:
    """按零件族路由生成 {mill, turn}. 不存在的键缺省."""
    _, _, size = _bbox_mm(stl_path)
    out = {}
    if family in ("roller", "shaft"):
        out["turn"] = generate_turning(spec, size)
    elif family in ("blade", "box_housing"):
        out["mill"] = generate_milling(spec, size, family)
    else:  # evolve 未知族: 按包围盒判定回转体
        if abs(size[0] - size[1]) < 0.05 * max(size[0], size[1]):
            out["turn"] = generate_turning(spec, size)
        else:
            out["mill"] = generate_milling(spec, size, family or "general")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stl"); ap.add_argument("outdir"); ap.add_argument("--family", default="")
    a = ap.parse_args()
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    res = generate(Path(a.stl), a.family, {})
    for k, v in res.items():
        p = out / v["name"]
        p.write_text(v["content"], encoding="utf-8")
        print(k, "ok", v["lines"], "lines ->", p)


if __name__ == "__main__":
    main()
