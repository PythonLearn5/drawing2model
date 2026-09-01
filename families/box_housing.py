# -*- coding: utf-8 -*-
"""
box_housing 零件族解释器 — OCP 参数化建模 worker
用法: python box_housing.py <spec.json> <out.stl>
spec.json 即 PartSpec (A+B 方案中 S5 只修改其数值字段):
{
 "part_type": "box_housing",
 "outer": {"L":455, "W":435, "H":455, "slope_deg":15, "slope_h":140},
 "bores": [ {"x":-227.5,"y":0,"z":250,"d":230,"len":134},
            {"x":-93.5,"y":0,"z":250,"d":215,"len":60},
            {"x":-33.5,"y":0,"z":250,"d":200,"len":261} ],
 "flange": {"x":-227.5,"y":0,"z":250,"d":260,"t":15,
            "holes":{"n":8,"pcd":220,"d":6.8,"depth":16}},
 "cavities": [ {"cx":-96.25,"cy":-96.25,"w":190,"d":190,"h":215,"r":10},
               {"cx":-96.25,"cy":96.25,"w":190,"d":190,"h":215,"r":10},
               {"cx":96.25,"cy":-96.25,"w":190,"d":190,"h":215,"r":10},
               {"cx":96.25,"cy":96.25,"w":190,"d":190,"h":215,"r":10} ],
 "top_holes": [ {"x":-187.5,"y":0,"d":10.2,"depth":24} ],
 "bottom_holes": [ {"x":-85,"y":0,"d":18,"depth":35} ]
}
蓝本: model_housing4.py (2026-08-24 手工迭代 v4)
"""
import sys, json, math
from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax2, gp_Trsf
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp

def fuse(a,b): return BRepAlgoAPI_Fuse(a,b).Shape()
def cut(a,b):  return BRepAlgoAPI_Cut(a,b).Shape()
def box(x,y,z,dx,dy,dz): return BRepPrimAPI_MakeBox(gp_Pnt(x,y,z),dx,dy,dz).Shape()
def cyl_x(x,y,z,r,Lt): return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x,y,z),gp_Dir(1,0,0)),r,Lt).Shape()
def cyl_z(x,y,z,r,Lt): return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x,y,z),gp_Dir(0,0,1)),r,Lt).Shape()
def fillet(sh,r):
    mk=BRepFilletAPI_MakeFillet(sh); ex=TopExp_Explorer(sh,TopAbs_EDGE)
    while ex.More():
        try: mk.Add(r,TopoDS.Edge_s(ex.Current()))
        except Exception: pass
        ex.Next()
    return mk.Shape()

def build(spec: dict):
    o = spec.get("outer", {})
    L = float(o.get("L", 455)); W = float(o.get("W", 435)); H = float(o.get("H", 455))
    slope_deg = float(o.get("slope_deg", 15)); slope_h = float(o.get("slope_h", 140))

    # 1) 基体: 带斜面五边形棱柱 (拓扑锁定: cut 孔/腔, 不再 fuse)
    # 关键修复: 水平距离 run 必须 <= L, 否则斜面顶点 x0+run 越过 x1 让基体越界
    # (此 bug 自 v1 即存在, 表现为实测 x_max 远大于 L/2)
    x0, x1 = -L/2, L/2
    run = min(slope_h / math.tan(math.radians(slope_deg)), L)
    poly = BRepBuilderAPI_MakePolygon()
    for p in [(x0+run,0),(x1,0),(x1,H),(x0,H),(x0,slope_h)]:
        poly.Add(gp_Pnt(p[0],0,p[1]))
    poly.Close()
    pr = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(poly.Wire()).Face(), gp_Vec(0,W,0)).Shape()
    tr = gp_Trsf(); tr.SetTranslation(gp_Vec(0,-W/2,0))
    body = BRepBuilderAPI_Transform(pr,tr,True).Shape()

    # 2) 轴承孔系 (subtractive, v1 教训)
    for b in spec.get("bores", []):
        body = cut(body, cyl_x(float(b["x"]), float(b.get("y",0)), float(b.get("z",250)),
                               float(b["d"])/2, float(b["len"])+20))

    # 3) 减重腔 (从底面挖, R10)
    for c in spec.get("cavities", []):
        w_, d_, h_, r_ = (float(c["w"]), float(c["d"]), float(c["h"]), float(c.get("r",10)))
        b_ = box(float(c["cx"])-w_/2, float(c["cy"])-d_/2, -1, w_, d_, h_+1)
        body = cut(body, fillet(b_, r_))

    # 4) 法兰 (唯一 additive)
    fl = spec.get("flange")
    if fl:
        fx, fz = float(fl.get("x",-L/2)), float(fl.get("z",250))
        body = fuse(body, cyl_x(fx-float(fl["t"]), 0, fz, float(fl["d"])/2, float(fl["t"])))
        ho = fl.get("holes", {})
        for i in range(int(ho.get("n",0))):
            a = math.radians(i*360/max(1,ho.get("n",1)) + 22.5)
            pcd = float(ho.get("pcd",220))/2
            body = cut(body, cyl_x(fx-float(fl["t"])-2, pcd*math.cos(a), fz+pcd*math.sin(a),
                                   float(ho.get("d",6.8))/2, float(ho.get("depth",16))+4))

    # 5) 顶/底面孔
    for h_ in spec.get("top_holes", []):
        body = cut(body, cyl_z(float(h_["x"]), float(h_.get("y",0)), H-float(h_["depth"]),
                               float(h_["d"])/2, float(h_["depth"])+1))
    for h_ in spec.get("bottom_holes", []):
        body = cut(body, cyl_z(float(h_["x"]), float(h_.get("y",0)), -2,
                               float(h_["d"])/2, float(h_["depth"])+2))
    return body

def main():
    spec_file, out_stl = sys.argv[1], sys.argv[2]
    spec = json.loads(open(spec_file, encoding="utf-8").read())
    body = build(spec)
    BRepMesh_IncrementalMesh(body, 0.4, False, 0.25, True)
    StlAPI_Writer().Write(body, out_stl)
    p = GProp_GProps(); BRepGProp.VolumeProperties_s(body, p)
    vol = p.Mass()
    print(json.dumps({"volume_mm3": vol, "mass_kg_ht250": vol*7.25e-6, "stl": out_stl}))

if __name__ == "__main__":
    main()
