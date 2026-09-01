# -*- coding: utf-8 -*-
"""
图纸无损高清化 — 解析 (S2 识别) 前置步骤
设计原则 (无损):
- 扫描型 PDF: 内嵌栅格图 (JPEG/PNG) 是图纸的"母版", 原样字节提取 (零处理零损失),
  绝不二次压缩/重编码降级.
- 矢量型 PDF: 矢量数据本身无损, 用高倍率光栅化 (渲染不是损失, 只是采样更密).
- 只在内嵌图短边超过 MAX_SHORT_EDGE 时做一次性 Lanczos 限幅 (控制 VLM token),
  并保留原字节 (source.<ext>) 备查.
产物 (写入 job 目录):
  overview_hd.png      高清总览 (识别/比对主图)
  tiles_hd_{ix}_{iy}.png 高清 4x2 分块
  enhance.json         元数据 {source, w, h, short_edge, tiles, limited}
"""
from __future__ import annotations
import json
from pathlib import Path

MAX_SHORT_EDGE = 3600   # 扫描型短边上限 (超过才限幅)
MAX_VECTOR_SHORT_EDGE = 2400  # 矢量型总览短边上限 (大图幅面动态降倍率, 防巨图)
TILE_ZOOM = 400 / 72.0  # 矢量分块渲染倍率 (~400 DPI, 远高于旧版 200)


def _extract_largest_image(doc) -> tuple[bytes, int, int, str] | None:
    """从扫描型 PDF 提取最大内嵌栅格图的原样字节 (无损).
    返回 (bytes, width, height, ext) 或 None."""
    import fitz
    best = None  # (area, xref)
    for im in doc[0].get_images(full=True):
        xref = im[0]
        try:
            info = doc.extract_image(xref)
        except Exception:
            continue
        area = info.get("width", 0) * info.get("height", 0)
        if best is None or area > best[0]:
            best = (area, xref)
    if best is None or best[0] <= 0:
        return None
    xref = best[1]
    try:
        info = doc.extract_image(xref)
        return info["image"], info["width"], info["height"], info["ext"]
    except Exception:
        return None


def _limit_short_edge(img_bytes: bytes, max_edge: int = MAX_SHORT_EDGE,
                      work_dir: Path | None = None) -> tuple[bytes, bool]:
    """短边超过上限时做一次性 Lanczos 限幅; 返回 (新字节, 是否限幅).
    未超限则原样返回 (保持无损)."""
    import cv2
    import numpy as np
    try:
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return img_bytes, False
        h, w = img.shape[:2]
        short = min(w, h)
        if short <= max_edge:
            return img_bytes, False
        scale = max_edge / float(short)
        img = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))),
                         interpolation=cv2.INTER_LANCZOS4)
        if work_dir is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            tmp = work_dir / "_limit_in.png"
            cv2.imwrite(str(tmp), img)
            out = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
        else:
            ok, buf = cv2.imencode(".png", img)
            out = buf.tobytes() if ok else img_bytes
        return out, True
    except Exception:
        return img_bytes, False


def _tile_hd(doc, page, out_dir: Path, zoom: float = TILE_ZOOM) -> int:
    """矢量高清分块: 4x2 网格, 每块按 zoom 倍率独立渲染 (无重叠损失)."""
    import fitz
    W, H = page.rect.width, page.rect.height
    n = 0
    for ix in range(4):
        for iy in range(2):
            clip = fitz.Rect(W * ix / 4, H * iy / 2, W * (ix + 1) / 4, H * (iy + 1) / 2)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            f = out_dir / f"tiles_hd_{ix}_{iy}.png"
            pix.save(str(f))
            n += 1
    return n


def enhance_drawing(pdf_path: Path, out_dir: Path) -> dict:
    """解析前无损高清化入口.
    返回元数据 {source, w, h, short_edge, tiles, limited, overview_hd}.
    失败不抛异常 — 调用方应回退到旧版 s1_preprocess 产物."""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"source": None, "w": 0, "h": 0, "short_edge": 0,
            "tiles": 0, "limited": False, "overview_hd": None}
    # 幂等: 已高清化过且产物仍在, 直接复用元数据
    mf = out_dir / "enhance.json"
    if mf.exists():
        try:
            prev = json.loads(mf.read_text("utf-8"))
            ov = prev.get("overview_hd")
            if ov and Path(ov).exists():
                return prev
        except Exception:
            pass
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        emb = _extract_largest_image(doc)
        if emb is not None and emb[1] * emb[2] > 500 * 500:
            # ---- 扫描型: 原样提取内嵌母版 (无损) ----
            img_bytes, w, h, ext = emb
            meta.update({"source": "embedded", "w": w, "h": h})
            src_f = out_dir / f"source.{ext}"
            src_f.write_bytes(img_bytes)  # 保留原字节备查
            limited_bytes, limited = _limit_short_edge(img_bytes, MAX_SHORT_EDGE, out_dir)
            meta["limited"] = limited
            # 未限幅 -> 保持原格式字节 (零重编码, 真正无损); 限幅后为 PNG
            ov_ext = "png" if limited else ext
            ov = out_dir / f"overview_hd.{ov_ext}"
            ov.write_bytes(limited_bytes)
            meta["overview_hd"] = str(ov)
            # 高清分块: 对总览按 4x2 网格切片 (来自原图, 无二次缩放)
            try:
                import cv2, numpy as np
                arr = np.frombuffer(limited_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
                if img is not None:
                    H2, W2 = img.shape[:2]
                    for ix in range(4):
                        for iy in range(2):
                            x0, x1 = W2 * ix // 4, W2 * (ix + 1) // 4
                            y0, y1 = H2 * iy // 2, H2 * (iy + 1) // 2
                            tile = img[y0:y1, x0:x1]
                            f = out_dir / f"tiles_hd_{ix}_{iy}.png"
                            cv2.imwrite(str(f), tile)
                            meta["tiles"] += 1
            except Exception:
                pass
        else:
            # ---- 矢量型: 无损渲染 (光栅化本身无损失), 倍率按短边上限动态收敛防巨图 ----
            meta["source"] = "vector"
            W, H = page.rect.width, page.rect.height
            # 上限优先: 短边不超过 MAX_VECTOR_SHORT_EDGE; 小图幅面最高 4x
            zoom = min(4.0, MAX_VECTOR_SHORT_EDGE / min(W, H))
            zoom = max(zoom, 1.2)  # 保底倍率 (小图不至于降采样)
            ov_pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            ov = out_dir / "overview_hd.png"
            ov_pix.save(str(ov))
            meta.update({"w": ov_pix.width, "h": ov_pix.height,
                         "zoom": round(zoom, 3), "overview_hd": str(ov)})
            meta["tiles"] = _tile_hd(doc, page, out_dir)
        meta["short_edge"] = min(meta["w"], meta["h"])
    except Exception as e:
        meta["error"] = str(e)
    try:
        (out_dir / "enhance.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass
    return meta
