# STL -> GLB (glTF 2.0 binary), JSON chunk 用空格填充, BIN chunk 用 0x00 填充
import struct, json, sys
import numpy as np
from stl import mesh

src = sys.argv[1] if len(sys.argv) > 1 else 'housing.stl'
dst = sys.argv[2] if len(sys.argv) > 2 else 'housing.glb'

m = mesh.Mesh.from_file(src)
verts = m.vectors.reshape(-1, 3).astype(np.float32)
v0, v1, v2 = m.vectors[:, 0], m.vectors[:, 1], m.vectors[:, 2]
n = np.cross(v1 - v0, v2 - v0)
ln = np.linalg.norm(n, axis=1, keepdims=True); ln[ln == 0] = 1
normals = np.repeat((n / ln).astype(np.float32), 3, axis=0)
idx = np.arange(len(verts), dtype=np.uint32)

pos_bin, nor_bin, idx_bin = verts.tobytes(), normals.tobytes(), idx.tobytes()

def pad(b, ch): return b + ch * ((4 - len(b) % 4) % 4)

bin_chunk = pad(pos_bin, b'\x00') + pad(nor_bin, b'\x00') + pad(idx_bin, b'\x00')
off_n = len(pad(pos_bin, b'\x00'))
off_i = off_n + len(pad(nor_bin, b'\x00'))

mn, mx = verts.min(0).tolist(), verts.max(0).tolist()
gltf = {
  'asset': {'version': '2.0', 'generator': 'workbuddy-stl2glb'},
  'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
  'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1}, 'indices': 2}]}],
  'accessors': [
    {'bufferView': 0, 'componentType': 5126, 'count': len(verts), 'type': 'VEC3', 'min': mn, 'max': mx},
    {'bufferView': 1, 'componentType': 5126, 'count': len(normals), 'type': 'VEC3'},
    {'bufferView': 2, 'componentType': 5125, 'count': len(idx), 'type': 'SCALAR'}],
  'bufferViews': [
    {'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos_bin)},
    {'buffer': 0, 'byteOffset': off_n, 'byteLength': len(nor_bin)},
    {'buffer': 0, 'byteOffset': off_i, 'byteLength': len(idx_bin)}],
  'buffers': [{'byteLength': len(bin_chunk)}]
}
js = pad(json.dumps(gltf, separators=(',', ':')).encode(), b' ')   # JSON chunk: 空格填充
total = 12 + 8 + len(js) + 8 + len(bin_chunk)
with open(dst, 'wb') as f:
    f.write(struct.pack('<III', 0x46546C67, 2, total))
    f.write(struct.pack('<II', len(js), 0x4E4F534A)); f.write(js)          # JSON
    f.write(struct.pack('<II', len(bin_chunk), 0x004E4942)); f.write(bin_chunk)  # BIN
print(f'{dst}: verts={len(verts)} json={len(js)}B bin={len(bin_chunk)}B')
