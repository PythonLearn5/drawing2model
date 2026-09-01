# -*- coding: utf-8 -*-
"""
shaft 零件族解释器 — 阶梯轴 OCP 参数化建模 worker
用法: python shaft.py <spec.json> <out.stl>

spec.json (PartSpec):
{
 "part_type": "shaft",
 "segments": [ {"d": 8, "len": 6}, {"d": 16.4, "len": 94}, {"d": 8, "len": 6} ],
 "bores":    [ {"x": 0, "d": 5, "len": 106} ],   # 可选轴向通孔
 "grooves":  [ {"x": 50, "w": 2, "depth": 1} ]   # 可选环形槽 (卡簧/退刀)
}
segments 沿 X 从左到右串联, 整体居中。
"""
import sys, json
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp

X = gp_Dir(1, 0, 0)


def cyl_x(x, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 0, 0), X), r, h).Shape()


def build(spec: dict):
    segs = spec.get("segments") or [{"d": 16, "len": 100}]
    total = sum(float(s["len"]) for s in segs)
    x = -total / 2
    body = None
    for s in segs:
        c = cyl_x(x, float(s["d"]) / 2, float(s["len"]))
        body = c if body is None else BRepAlgoAPI_Fuse(body, c).Shape()
        x += float(s["len"])

    for g in spec.get("grooves", []):
        gx, gw, gd = float(g["x"]), float(g.get("w", 2)), float(g.get("depth", 1))
        # 环槽: 用大圆柱减 — 外圈留材料被切除: 直接 cut 一个厚 gw 的短圆柱(半径=当前外径)
        # 简化: 用一个半径足够大的圆柱壳 cut, 这里用"cut 外环"方式: 先求该段外径近似用最大段半径
        rmax = max(float(s["d"]) / 2 for s in segs)
        ring = BRepAlgoAPI_Cut(cyl_x(gx - gw / 2, rmax + 1, gw),
                               cyl_x(gx - gw / 2 - 1, rmax - gd, gw + 2)).Shape()
        body = BRepAlgoAPI_Cut(body, ring).Shape()

    for b in spec.get("bores", []):
        body = BRepAlgoAPI_Cut(body, cyl_x(float(b["x"]), float(b["d"]) / 2,
                                           float(b.get("len", total)))).Shape()
    return body


def main():
    spec_file, out_stl = sys.argv[1], sys.argv[2]
    spec = json.loads(open(spec_file, encoding="utf-8").read())
    body = build(spec)
    BRepMesh_IncrementalMesh(body, 0.05, False, 0.25, True)
    StlAPI_Writer().Write(body, out_stl)
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(body, p)
    vol = p.Mass()
    print(json.dumps({"volume_mm3": vol, "mass_kg_steel": vol * 7.85e-6, "stl": out_stl}))


if __name__ == "__main__":
    main()
