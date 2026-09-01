# -*- coding: utf-8 -*-
"""STL -> STEP 导出 (OCP 环境: 读回网格 -> 缝合 -> STEPControl 写出)
CLI: python step_export.py <in.stl> <out.step>"""
import sys, time
from OCP.TopoDS import TopoDS_Shape
from OCP.StlAPI import StlAPI_Reader
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone

src, dst = sys.argv[1], sys.argv[2]
t0 = time.time()
sh = TopoDS_Shape()
if not StlAPI_Reader().Read(sh, src):
    print("READ_FAIL"); sys.exit(1)
sew = BRepBuilderAPI_Sewing(1e-3, True, True, True, False)
sew.Add(sh)
sew.Perform()
w = STEPControl_Writer()
w.Transfer(sew.SewedShape(), STEPControl_AsIs)
ok = w.Write(dst) == IFSelect_RetDone
print("STEP_OK" if ok else "STEP_FAIL", f"{time.time()-t0:.1f}s")
sys.exit(0 if ok else 1)
