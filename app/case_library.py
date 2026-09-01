# -*- coding: utf-8 -*-
"""
案例库 (Case Library) — A+B 融合方案的 B 部分
把「人工/自动迭代的决策过程」结构化入库:
  case = {症状(差异描述), 上下文(零件族/特征), 修正动作(参数diff或拓扑修正), 结果(score变化)}
检索: 简单关键词+特征类型匹配 (体量小, 不需要向量库; 后续可换 embedding)
种子: 体壳 v1->v4 的 4 次手工迭代 (2026-08-24 会话完整记录)
"""
from __future__ import annotations
import json
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent.parent / "cases"

SEED_CASES = [
    {
        "id": "housing-v1",
        "family": "box_housing",
        "symptom": "轴承孔位置出现外凸圆柱, 模型呈鼓包状; 正视图顶部被整体挖空",
        "feature": "stepped_bore",
        "error_type": "topology",
        "fix": {"action": "topology", "detail": "轴承孔必须用布尔 cut 从基体切除, 不能 fuse 圆柱。负特征(孔/腔)一律 cut。"},
        "lesson": "VLM 识别出的孔类特征在 CSG 中永远是 subtractive, 不是 additive",
        "score_before": 0.3, "score_after": 0.55
    },
    {
        "id": "housing-v2",
        "family": "box_housing",
        "symptom": "主体宽度 290mm 明显偏窄, 与 H 向视图(435x145)不符; 减重腔深度不足, 腔未贯穿到主轴下方",
        "feature": "outer_body",
        "error_type": "parameter",
        "fix": {"action": "param", "detail": "body W: 290 -> 435 (以 H 向视图为准, 纵剖视图的宽度方向不可靠)"},
        "lesson": "宽度/深度方向的尺寸要看侧视或横剖视图, 纵剖只给长度和高度",
        "score_before": 0.55, "score_after": 0.7
    },
    {
        "id": "housing-v3",
        "family": "box_housing",
        "symptom": "上部内腔挖空范围过大, 削弱了轴承孔所在的实体区域; 腔与孔相互干扰",
        "feature": "cavity_grid",
        "error_type": "parameter+order",
        "fix": {"action": "order+param", "detail": "先成型基体 -> cut 三段孔 -> cut 减重腔。内腔挖空只到主轴中心线下方, 上部保持实体仅让孔穿过。腔深 215 逼近主轴中心线(250)即可。"},
        "lesson": "CSG 顺序: 基体->主孔系->辅助腔。挖空区域不能侵入轴承孔配合段实体",
        "score_before": 0.7, "score_after": 0.85
    },
    {
        "id": "housing-v4",
        "family": "box_housing",
        "symptom": "减重腔 180x180 偏小, 与图纸 2x2 大腔比例不符",
        "feature": "cavity_grid",
        "error_type": "parameter",
        "fix": {"action": "param", "detail": "CAV: 180 -> 190, 腔间距按图纸 182.5 中心距核算"},
        "lesson": "减重腔尺寸从底面视图(D-D)读取, 注意是内腔尺寸还是中心距",
        "score_before": 0.85, "score_after": 0.93
    },
]

_case_index: list[dict] | None = None

def _load() -> list[dict]:
    global _case_index
    if _case_index is None:
        _case_index = list(SEED_CASES)
        # 加载磁盘上的增量案例
        if CASES_DIR.exists():
            for f in sorted(CASES_DIR.glob("*.json")):
                try: _case_index.append(json.loads(f.read_text("utf-8")))
                except Exception: pass
    return _case_index

def search(query: str, family: str = "", top_k: int = 3) -> list[dict]:
    """关键词检索: symptom/feature/lesson 与 query 的词重叠度"""
    cases = _load()
    qwords = set(_tokenize(query)) | set(_tokenize(query, bigram=True))
    scored = []
    for c in cases:
        if family and c.get("family") != family: continue
        text = " ".join([c.get("symptom",""), c.get("feature",""), c.get("lesson","")])
        cwords = set(_tokenize(text)) | set(_tokenize(text, bigram=True))
        overlap = len(qwords & cwords)
        if overlap > 0:
            scored.append((overlap, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]

def add_case(case: dict) -> str:
    """运行时沉淀新案例 (S5 每轮迭代结束自动调用)"""
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    cid = case.get("id") or f"case-{len(_load())+1}"
    case["id"] = cid
    (CASES_DIR / f"{cid}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), "utf-8")
    _case_index = None  # 失效重载
    return cid

def _tokenize(s: str, bigram: bool = False) -> list[str]:
    if bigram:
        return [s[i:i+2] for i in range(len(s)-1)]
    return [w for w in __import__("re").split(r"[\s,，;；/、。:：]+", s) if w]
