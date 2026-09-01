# -*- coding: utf-8 -*-
"""
产物格式转换 — STL -> OBJ / DXF / 三视图投影(亮/暗)
CLI: python convert.py <in.stl> <out_dir> [--name base]
STEP 导出见 step_export.py (需 OCP 环境)。
DWG 为 Autodesk 私有格式, 以 DXF (AutoCAD 交换格式) 交付, CAD 软件可直接打开/另存。
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import numpy as np
from stl import mesh as stlmesh

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


def stl_to_obj(stl_path: Path, obj_path: Path) -> None:
    """STL -> OBJ (三角形面, 顶点去重)."""
    m = stlmesh.Mesh.from_file(str(stl_path))
    verts = m.vectors.reshape(-1, 3)
    # 顶点去重
    uniq, inv = np.unique(np.round(verts, 5), axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3) + 1
    with open(obj_path, "w", encoding="utf-8") as f:
        f.write("# Drawing2Model STL->OBJ export\n")
        f.write(f"o model\n")
        for v in uniq:
            f.write(f"v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
        for tri in faces:
            f.write(f"f {tri[0]} {tri[1]} {tri[2]}\n")


def _edges_2d(verts3: np.ndarray, drop_axis: int, mn, mx, max_segs=40000) -> list:
    """把三角形网格投影到 2D, 只保留轮廓/折线边 (简化: 全部投影边, 限数量)."""
    ax = [i for i in range(3) if i != drop_axis]
    segs = []
    step = max(1, len(verts3) // max_segs)
    for tri in verts3[::step]:
        p = tri[:, ax]
        segs.append((p[0], p[1])); segs.append((p[1], p[2])); segs.append((p[2], p[0]))
    return segs


def projections(stl_path: Path, out_dir: Path, dark: bool) -> Path:
    """三视图正交投影 (前视/俯视/侧视), 亮色或暗色版."""
    m = stlmesh.Mesh.from_file(str(stl_path))
    vecs = m.vectors
    if len(vecs) > 80000:
        idx = np.random.default_rng(7).choice(len(vecs), 80000, replace=False)
        vecs = vecs[idx]
    mn = vecs.reshape(-1, 3).min(0); mx = vecs.reshape(-1, 3).max(0)
    span = (mx - mn).max()

    bg = "#0d1117" if dark else "#f5f7fa"
    fg = "#9fb6d8" if dark else "#4a5568"
    edge = "#3b82f6" if dark else "#1f2937"

    fig = plt.figure(figsize=(15, 10), facecolor=bg)
    views = [("前视图 FRONT", 0, -90), ("俯视图 TOP", 90, -90), ("侧视图 SIDE", 0, 0)]
    for i, (title, elev, azim) in enumerate(views):
        ax = fig.add_subplot(2, 2, i + 1, projection="3d", facecolor=bg)
        poly = Poly3DCollection(vecs, alpha=1.0, facecolor=fg,
                                edgecolor=edge, linewidths=0.04)
        ax.add_collection3d(poly)
        ax.set_xlim(mn[0], mx[0]); ax.set_ylim(mn[1], mx[1]); ax.set_zlim(mn[2], mx[2])
        ax.set_box_aspect(mx - mn)
        ax.view_init(elev=elev, azim=azim)
        ax.set_proj_type("ortho")
        ax.set_title(title, color="#e6edf3" if dark else "#111827", fontsize=13)
        ax.axis("off")
    # 等轴测
    ax = fig.add_subplot(2, 2, 4, projection="3d", facecolor=bg)
    poly = Poly3DCollection(vecs, alpha=1.0, facecolor=fg,
                            edgecolor=edge, linewidths=0.04)
    ax.add_collection3d(poly)
    ax.set_xlim(mn[0], mx[0]); ax.set_ylim(mn[1], mx[1]); ax.set_zlim(mn[2], mx[2])
    ax.set_box_aspect(mx - mn)
    ax.view_init(elev=-30, azim=45)
    ax.set_proj_type("ortho")
    ax.set_title("等轴测 ISO", color="#e6edf3" if dark else "#111827", fontsize=13)
    ax.axis("off")
    fig.suptitle(f"三视图正交投影 ({'暗色' if dark else '亮色'})  尺寸范围 "
                 f"{span:.1f} mm", color="#7db4ff" if dark else "#374151", fontsize=14)
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = out_dir / ("proj_dark.png" if dark else "proj_light.png")
    plt.savefig(out, dpi=100, facecolor=bg); plt.close(fig)
    return out


def stl_to_dxf(stl_path: Path, dxf_path: Path) -> None:
    """STL -> DXF (三视图投影轮廓线, LWPOLYLINE 段).
    DWG 为私有二进制格式, DXF 是其文本交换格式, 所有 CAD 可打开."""
    m = stlmesh.Mesh.from_file(str(stl_path))
    vecs = m.vectors
    if len(vecs) > 30000:
        idx = np.random.default_rng(7).choice(len(vecs), 30000, replace=False)
        vecs = vecs[idx]
    views = [("FRONT", 0), ("TOP", 2), ("SIDE", 1)]  # drop axis
    lines = ["0", "SECTION", "2", "HEADER", "0", "ENDSEC",
             "0", "SECTION", "2", "TABLES", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES"]
    offsets = {"FRONT": (0, 0), "TOP": (0, -500), "SIDE": (1000, 0)}
    for name, drop in views:
        ox, oy = offsets[name]
        ax = [i for i in range(3) if i != drop]
        for tri in vecs:
            p = tri[:, ax]
            for a, b in [(0, 1), (1, 2), (2, 0)]:
                lines += ["0", "LINE", "8", name,
                          "10", f"{p[a][0] + ox:.3f}", "20", f"{p[a][1] + oy:.3f}",
                          "11", f"{p[b][0] + ox:.3f}", "21", f"{p[b][1] + oy:.3f}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    dxf_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stl"); ap.add_argument("outdir"); ap.add_argument("--name", default="model")
    a = ap.parse_args()
    stl, out = Path(a.stl), Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    stl_to_obj(stl, out / f"{a.name}.obj")
    print("obj ok")
    projections(stl, out, dark=False); print("proj_light ok")
    projections(stl, out, dark=True); print("proj_dark ok")
    stl_to_dxf(stl, out / f"{a.name}.dxf")
    print("dxf ok")


if __name__ == "__main__":
    main()
