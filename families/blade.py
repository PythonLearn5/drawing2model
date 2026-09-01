# -*- coding: utf-8 -*-
"""
blade 零件族解释器 — OCP 截面放样建模 worker (移植自 engineering_service/geometry_builders.py 的 loft 逻辑)
用法: python blade.py <spec.json> <out.stl>

spec.json (PartSpec):
{
 "part_type": "blade",
 "span": 500,                  # 径向展向总长 (截面间放样距离)
 "chord": 200,                 # 弦长 (默认用于无 sections 时的参数化翼型)
 "sections": [                 # 可选: 成品尺寸截面坐标表 (同图纸叶型尺寸表)
    {"x_pct": [1.25,2.5,...],  # 弦向百分比
     "y":     [6.90,9.73,...], # 该站厚度 (成品尺寸), 点数须一致
     "twist_deg": 0.0,         # 该站叠合/扭转角 (绕展向轴)
     "offset": [0.0, 0.0]      # 该站在截面平面内的平移 [dx, dy]
    }, ...],
 "root": {"w": 80, "t": 60, "h": 60},   # 叶根矩形榫头 (可选)
 "stack": "linear"             # 截面沿 z 均匀分布
}
无 sections 时退化为对称圆弧翼型参数化 (chord + max_t 占厚比)。
蓝本: 叶片_DA16+5.pdf 叶型尺寸表 + 旧 loft 实现
"""
import sys, json, math
from OCP.gp import gp_Pnt, gp_Vec, gp_Ax1, gp_Trsf
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp


def _closed_bspline_wire(pts2d, z, twist_deg=0.0, off=(0.0, 0.0)):
    """2D 闭合点列 -> 3D 截面 wire (在 z 平面, 绕 z 轴扭转, 平移)."""
    c, s = math.cos(math.radians(twist_deg)), math.sin(math.radians(twist_deg))
    pts = []
    for (x, y) in pts2d:
        xr = x * c - y * s + off[0]
        yr = x * s + y * c + off[1]
        pts.append(gp_Pnt(xr, yr, z))
    n = len(pts)
    arr = TColgp_HArray1OfPnt(1, n)
    for i, p in enumerate(pts):
        arr.SetValue(i + 1, p)
    interp = GeomAPI_Interpolate(arr, True, 1e-6)  # periodic -> 闭合
    interp.Perform()
    curve = interp.Curve()
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve).Edge()).Wire()


def _airfoil_pts(x_pct, y_thk, chord):
    """坐标表 (x%, 总厚 y) -> 对称翼型闭合 2D 点列 (前缘在 0)."""
    up = [(0.0, 0.0)] + [(x / 100.0 * chord, t / 2.0) for x, t in zip(x_pct, y_thk)]
    dn = [(x / 100.0 * chord, -t / 2.0) for x, t in reversed(list(zip(x_pct, y_thk)))]
    return up + dn


def _default_sections(chord, n_sec=6, max_t_ratio=0.18):
    """无坐标表时的参数化对称翼型截面 (圆弧厚度分布)."""
    secs = []
    xs = [1.25, 2.5, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]
    for i in range(n_sec):
        f = 1.0 - 0.4 * (i / max(1, n_sec - 1))  # 叶根厚 -> 叶尖薄
        y = [max_t_ratio * chord * f * math.sqrt(max(0.0, x / 100.0)) * (1 - x / 130.0)
             for x in xs]
        y = [max(2.0, v) for v in y]
        secs.append({"x_pct": xs, "y": y, "twist_deg": 12.0 * i / max(1, n_sec - 1)})
    return secs


def build(spec: dict):
    span = float(spec.get("span", 500))
    chord = float(min(max(spec.get("chord", 200), 50), 3000))
    secs = spec.get("sections") or []

    # 鲁棒性: VLM 可能只给 1 个截面 -> 厚度递减+扭转递增补成多截面
    if len(secs) == 1:
        base = secs[0]
        secs = []
        for i, (thk_f, tw) in enumerate([(1.0, 0), (0.85, 8), (0.65, 16), (0.4, 24)]):
            secs.append({"x_pct": base["x_pct"],
                         "y": [max(2.0, v * thk_f) for v in base["y"]],
                         "twist_deg": float(base.get("twist_deg", 0)) + tw})
    if not secs:
        secs = _default_sections(chord)

    n = len(secs)

    wires = []
    for i, sec in enumerate(secs):
        z = span * i / max(1, n - 1)
        pts = _airfoil_pts(sec["x_pct"], sec["y"], chord)
        # 截面居中: 弦向中心对齐
        pts = [(x - chord / 2, y) for (x, y) in pts]
        wires.append(_closed_bspline_wire(pts, z,
                                          float(sec.get("twist_deg", 0)),
                                          tuple(sec.get("offset", (0, 0)))))

    th = BRepOffsetAPI_ThruSections(True, False)  # solid, 非直纹(光顺)
    for w in wires:
        th.AddWire(w)
    th.Build()
    body = th.Shape()

    # 叶根榫头 (可选, fuse 到 z=0 以下), 尺寸 clamp 防 VLM 给错量级
    rt = spec.get("root")
    if rt:
        w_ = min(float(rt.get("w", 80)), chord * 0.6)
        t_ = min(float(rt.get("t", 60)), 200)
        h_ = min(float(rt.get("h", 60)), 200)
        box_ = BRepPrimAPI_MakeBox(gp_Pnt(-w_ / 2, -t_ / 2, -h_), w_, t_, h_ + 1).Shape()
        body = BRepAlgoAPI_Fuse(body, box_).Shape()
    return body


def main():
    spec_file, out_stl = sys.argv[1], sys.argv[2]
    spec = json.loads(open(spec_file, encoding="utf-8").read())
    body = build(spec)
    BRepMesh_IncrementalMesh(body, 0.4, False, 0.25, True)
    StlAPI_Writer().Write(body, out_stl)
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(body, p)
    vol = p.Mass()
    print(json.dumps({"volume_mm3": vol, "mass_kg_2cr13": vol * 7.75e-6, "stl": out_stl}))


if __name__ == "__main__":
    main()
