# -*- coding: utf-8 -*-
"""生成一张简单的阶梯轴工程图 PDF (供 drawing2model 测试用)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_pdf import PdfPages
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "output" / "sample_shaft.pdf"

def draw():
    fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A3 横向
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(left=0.05, right=0.95, top=0.92, bottom=0.08)

    # ---- 图框 ----
    ax.add_patch(Rectangle((0, 0), 290, 200, fill=False, lw=1.2))
    # 标题栏
    ax.add_patch(Rectangle((170, 0), 120, 28, fill=False, lw=0.8))
    ax.text(230, 22, "阶梯轴  SHAFT-001", ha="center", va="center", fontsize=9, fontweight="bold")
    ax.text(180, 14, "图号 DWG-2024-001", fontsize=7)
    ax.text(180, 8,  "比例 1:2    材料 45#", fontsize=7)
    ax.text(250, 14, "单位: mm", fontsize=7)
    ax.text(250, 8,  "设计: d2m", fontsize=7)

    # ---- 阶梯轴主视图 (水平放置, 以 x=150 为中心) ----
    cx = 150
    base_y = 110  # 轴线 y
    # 三段阶梯: 左 φ20×40, 中 φ32×60, 右 φ20×40, 总长 140
    segs = [
        (cx - 70, 40, 20, "20"),   # 左段
        (cx - 30, 60, 32, "32"),   # 中段
        (cx + 30, 40, 20, "20"),   # 右段
    ]
    for x, w, d, _ in segs:
        y0 = base_y - d / 2
        ax.add_patch(Rectangle((x, y0), w, d, fill=False, lw=1.5))

    # 退刀槽 (中段两端, 宽 3 深 2)
    for sx in (cx - 30, cx + 30 - 3):
        ax.plot([sx, sx], [base_y - 16, base_y - 20], lw=1.0, color="gray")
        ax.plot([sx, sx], [base_y + 16, base_y + 20], lw=1.0, color="gray")

    # 中心线
    ax.plot([cx - 80, cx + 80], [base_y, base_y], color="blue", lw=0.6, ls="--")

    # ---- 尺寸标注 ----
    def dim_h(x1, x2, y, text):
        ax.annotate("", xy=(x2, y), xytext=(x1, y),
                    arrowprops=dict(arrowstyle="<->", lw=0.8))
        ax.text((x1 + x2) / 2, y + 3, text, ha="center", fontsize=8)

    def dim_v(x, y1, y2, text):
        ax.annotate("", xy=(x, y2), xytext=(x, y1),
                    arrowprops=dict(arrowstyle="<->", lw=0.8))
        ax.text(x + 2, (y1 + y2) / 2, text, fontsize=8, rotation=90, va="center")

    # 总长
    dim_h(cx - 70, cx + 70, base_y + 30, "140")
    # 各段长度
    dim_h(cx - 70, cx - 30, base_y - 20, "40")
    dim_h(cx - 30, cx + 30, base_y - 20, "60")
    dim_h(cx + 30, cx + 70, base_y - 20, "40")
    # 各段直径 (引出线)
    for x, w, d, dia in segs:
        ex = x + w / 2
        ax.plot([ex, ex + 15], [base_y + d/2, base_y + d/2 + 10], lw=0.6)
        ax.plot([ex, ex], [base_y + d/2, base_y + d/2 + 10], lw=0.6)
        ax.text(ex + 16, base_y + d/2 + 10, f"φ{dia}", fontsize=8)

    # ---- 倒角标注 ----
    ax.text(cx - 65, base_y + 22, "C1.5", fontsize=7, color="gray")
    ax.text(cx + 58, base_y + 22, "C1.5", fontsize=7, color="gray")

    # ---- 剖面 A-A (键槽截面, 放在右下) ----
    sx, sy = 230, 60
    ax.add_patch(plt.Circle((sx, sy), 16, fill=False, lw=1.5))
    ax.plot([sx, sx + 40], [sy, sy], color="blue", lw=0.6, ls="--")
    ax.plot([sx, sx], [sy - 25, sy + 25], color="blue", lw=0.6, ls="--")
    ax.text(sx + 42, sy, "A", fontsize=8)
    ax.text(sx, sy + 28, "A-A", fontsize=8, ha="center")
    # 键槽
    ax.add_patch(Rectangle((sx - 6, sy + 12), 12, 4, fill=False, lw=1.5))
    dim_v(sx + 20, sy, sy + 16, "16")
    ax.text(sx - 10, sy + 30, "键槽 6×3×16", fontsize=7)

    # ---- 技术要求 ----
    ax.text(10, 50, "技术要求:", fontsize=8, fontweight="bold")
    reqs = [
        "1. 未注尺寸公差按 GB/T 1804-m;",
        "2. 调质处理 28~32 HRC;",
        "3. 键槽两侧面对轴线的对称度公差 0.02mm;",
        "4. 去除毛刺锐边倒钝;",
    ]
    for i, r in enumerate(reqs):
        ax.text(10, 44 - i * 5, r, fontsize=7)

    ax.set_xlim(-5, 295)
    ax.set_ylim(-5, 205)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(OUT) as pdf:
        pdf.savefig(fig)
    plt.close(fig)
    print(f"已生成: {OUT}")

if __name__ == "__main__":
    draw()
