# -*- coding: utf-8 -*-
"""
roller 零件族解释器 — 行星滚柱 OCP 参数化建模 worker
蓝本: 4805-2行星滚柱.pdf (GZ4805-B, GCr15)
用法: python roller.py <spec.json> <out.stl>

spec.json (PartSpec):
{
 "part_type": "roller",
 "L": 106, "body_L": 94,
 "major_d": 16.436,            # 滚柱大径 (螺纹节圆直径)
 "minor_d": 16.26,             # 滚道底直径 (缺省 major-2*groove_depth)
 "lead": 16, "pitch": 1,       # 导程/螺距 (特16x1)
 "groove_depth": 0.6,          # 滚道槽深 (缺省 (major-minor)/2)
 "journal": {"d": 8, "len": 6} # 两端轴颈 (2-φ8)
}
建模: 大径基体圆柱 - 螺旋滚道槽 (PipeShell 沿螺旋线扫掠 V 形槽) + 两端轴颈
"""
import sys, json, math
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge,
                                BRepBuilderAPI_MakePolygon)
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp

Z = gp_Dir(0, 0, 1)


def cyl(x, y, z, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), Z), r, h).Shape()


def _helix_wire(r, pitch, height, z_start, n=500):
    """采样螺旋线 -> BSpline wire (无 MakeHelix 时的等价实现)."""
    turns = height / pitch
    arr = TColgp_HArray1OfPnt(1, n + 1)
    for i in range(n + 1):
        t = i / n
        th = 2 * math.pi * turns * t
        arr.SetValue(i + 1, gp_Pnt(r * math.cos(th), r * math.sin(th), z_start + height * t))
    interp = GeomAPI_Interpolate(arr, False, 1e-6)
    interp.Perform()
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()).Wire()


def _groove_wire(r, depth, width, z_at):
    """V 形滚道槽剖面 (闭合线框), 尖端朝内, 位于螺旋起点 (r,0,z_at) 处 XZ 平面."""
    tip = gp_Pnt(r - depth, 0, z_at)
    ol = gp_Pnt(r + depth, 0, z_at - width / 2)
    om = gp_Pnt(r + depth * 1.3, 0, z_at)
    orr = gp_Pnt(r + depth, 0, z_at + width / 2)
    poly = BRepBuilderAPI_MakePolygon()
    for p in (tip, ol, om, orr, tip):
        poly.Add(p)
    poly.Close()
    return poly.Wire()


def build(spec: dict):
    L = float(spec.get("L", 106))
    body_L = float(spec.get("body_L", 94))
    major_d = float(spec.get("major_d", 16.436))
    pitch = float(spec.get("pitch", 1))
    default_depth = max((major_d - float(spec.get("minor_d", major_d - 0.18))) / 2, 0.088)
    depth = float(spec.get("groove_depth", default_depth))
    jn = spec.get("journal", {"d": 8, "len": 6})

    r = major_d / 2
    z0, z1 = (L - body_L) / 2, (L + body_L) / 2

    # 1) 基体 (大径圆柱)
    body = cyl(0, 0, z0, r, body_L)

    # 2) 螺旋滚道槽: 螺旋线从 z0+pitch 起, 剖面置于螺旋起点
    zs = z0 + pitch
    h_sweep = body_L - 2 * pitch
    helix = _helix_wire(r, pitch, h_sweep, zs)
    profile = _groove_wire(r, depth, pitch * 0.85, zs)
    ps = BRepOffsetAPI_MakePipeShell(helix)
    ps.SetMode(True)      # Frenet
    ps.Add(profile)
    ps.Build()
    if ps.IsReady():
        ps.MakeSolid()
        body = BRepAlgoAPI_Cut(body, ps.Shape()).Shape()

    # 3) 两端轴颈 (2-φ8 x 6)
    body = BRepAlgoAPI_Fuse(body, cyl(0, 0, 0, float(jn.get("d", 8)) / 2, float(jn.get("len", 6)))).Shape()
    body = BRepAlgoAPI_Fuse(body, cyl(0, 0, z1, float(jn.get("d", 8)) / 2, float(jn.get("len", 6)))).Shape()
    return body


def main():
    spec_file, out_stl = sys.argv[1], sys.argv[2]
    spec = json.loads(open(spec_file, encoding="utf-8").read())
    body = build(spec)
    BRepMesh_IncrementalMesh(body, 0.05, False, 0.25, True)
    StlAPI_Writer().Write(body, out_stl)
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(body, p)
    vol = p.Mass()
    print(json.dumps({"volume_mm3": vol, "mass_kg_gcr15": vol * 7.81e-6, "stl": out_stl}))


if __name__ == "__main__":
    main()
