# 渲染 STL 多视角用于与图纸比对
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from stl import mesh

import sys
m = mesh.Mesh.from_file(sys.argv[1] if len(sys.argv)>1 else "housing.stl")
print('triangles:', len(m.vectors))

# 包围盒
mn = m.vectors.reshape(-1,3).min(0); mx = m.vectors.reshape(-1,3).max(0)
print('bbox min', mn, 'max', mx, 'size', mx-mn)

def view(elev, azim, fname, title):
    fig = plt.figure(figsize=(10,8))
    ax = fig.add_subplot(111, projection='3d')
    # 降采样三角形以加速
    vecs = m.vectors
    if len(vecs) > 60000:
        idx = np.random.choice(len(vecs), 60000, replace=False)
        vecs = vecs[idx]
    poly = Poly3DCollection(vecs, alpha=1.0, facecolor='#8a919c', edgecolor='#333333', linewidths=0.05)
    ax.add_collection3d(poly)
    ax.set_xlim(mn[0], mx[0]); ax.set_ylim(mn[1], mx[1]); ax.set_zlim(mn[2], mx[2])
    ax.set_box_aspect((mx-mn))
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(title)
    ax.set_xlabel('X (主轴)'); ax.set_ylabel('Y'); ax.set_zlabel('Z')
    plt.tight_layout(); plt.savefig(fname, dpi=90); plt.close()
    print('saved', fname)

import os
outdir = sys.argv[2] if len(sys.argv)>2 else '.'
os.makedirs(outdir, exist_ok=True)
def _p(n): return os.path.join(outdir, n)
view(0, -90, _p('v_front.png'), 'Front (A-A 方向)')
view(90, -90, _p('v_top.png'), 'Top')
view(0, 0, _p('v_side.png'), 'Side')
view(-40, 30, _p('v_bottom.png'), 'Bottom-ish')
