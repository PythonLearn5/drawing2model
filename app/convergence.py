# -*- coding: utf-8 -*-
"""
S5 收敛验证回路 (A+B 融合)
A: 渲染截图 vs 原图纸 -> LLM 视觉比对 -> 差异JSON + score
B: 差异症状 -> 案例库检索 -> 历史修正经验 -> 参数修正器决策
离线降级: 无 LLM 时用几何规则验证(包围盒/孔径/体积比) + 案例库直接给修正建议
"""
from __future__ import annotations
import json, math
from pathlib import Path
from llm.gateway import get_gateway
from app import case_library

COMPARE_SYSTEM = """你是机械工程图审查专家。
图像输入顺序:  [0] 原图样扫描/工程图  [1..N] 重建后的3D模型多视角渲染图.
你的任务: 对比原图样 [0] 与3D模型 [1..N] 的几何差异, 判断3D模型相对图纸是否正确.
特别注意: 图纸才是真值参考; 3D模型必须与图纸一致才算正确. 若3D模型缺失图纸上的特征(如通孔/腔/凸台/斜面等)即为差异.
输出 JSON:
{
 "score": 0到1的相似度(1.0=完全匹配),
 "issues": [
   {"part": "3D模型中的特征名", "symptom": "差异描述 (说明3D模型与图纸的差距)", "suggestion": "参数修改建议, 如 'CAV_H 215->240'", "severity": "high|mid|low"}
 ]
}
只关注几何形状: 外形轮廓、孔的数量/位置/直径、腔体、凸台、斜面。忽略渲染风格、颜色、阴影。"""

REPAIR_SYSTEM = """你是参数化CAD修正器。输入: 当前PartSpec参数、比对差异JSON、检索到的历史案例。
输出修正后的参数diff JSON: {"params": {"键": 新值, ...}, "reason": "依据案例xxx"}。
硬约束:
1. 只允许修改数值型参数(尺寸/角度/位置/数量), 禁止增删特征或改变特征类型
2. 每个参数单轮修正幅度不超过30%
3. 若差异属于拓扑问题(特征缺失/多出/类型错), 不要修参数, 输出 {"topology_issue": true, "detail": "..."}"""


async def validate_round(render_imgs: list[bytes], drawing_img: bytes) -> dict:
    """一轮视觉验证: 返回 {score, issues[]} 或规则降级结果
    图像顺序: [0] 原图样 (真值参考), [1..N] 重建模型多视角渲染."""
    gw = get_gateway()
    if gw.online:
        result = await gw.vision_json(
            [drawing_img] + render_imgs, COMPARE_SYSTEM,
            "请按 SYSTEM 中定义的图像顺序(0=原图样, 1..N=3D模型渲染) 输出比对JSON。")
        if result and "score" in result:
            return result
    # 离线降级: 无 score, 走几何规则 (调用方用 spec_rules 补充)
    return {"score": None, "issues": [], "offline": True}


def spec_rules_check(spec: dict, measured: dict, truth: dict | None = None,
                     family: str = "box_housing") -> list[dict]:
    """确定性规则验证.
    truth (可选): 离线模式的真值基线. 若提供, 优先用 truth 比 spec (识别结果可能错);
    否则用 spec vs 实测 (自洽校验, 不能纠错).
    返回的 suggestion 用完整嵌套路径 (outer.W / cavities[0].w / bores[1].d) 供修复器直接采纳.
    family 决定主尺寸映射: box_housing 用 outer.L/W/H 对 dx/dy/dz;
    roller/shaft 用 L 对 max 轴; blade 用 span 对 dz / chord 对 max(dx,dy)."""
    issues = []
    bbox = measured.get("bbox", {})
    size = measured.get("size", {})

    if family in ("roller", "shaft"):
        want = float(spec.get("L") or spec.get("outer", {}).get("L") or 0)
        got = max(size.get("dx", 0), size.get("dy", 0), size.get("dz", 0))
        if want and got and abs(want - got) > max(2.0, want * 0.02):
            issues.append({"part": "L", "symptom": f"总长实测{got:.0f}与目标{want}偏差过大",
                           "suggestion": f"L -> {want}", "severity": "high"})
        return issues
    if family == "blade":
        want_span = float(spec.get("span") or 0)
        got_span = size.get("dz", 0)
        if want_span and got_span and abs(want_span - got_span) > max(2.0, want_span * 0.02):
            issues.append({"part": "span", "symptom": f"展向实测{got_span:.0f}与目标{want_span}偏差过大",
                           "suggestion": f"span -> {want_span}", "severity": "high"})
        want_chord = float(spec.get("chord") or 0)
        got_chord = max(size.get("dx", 0), size.get("dy", 0))
        if want_chord and got_chord and abs(want_chord - got_chord) > max(2.0, want_chord * 0.05):
            issues.append({"part": "chord", "symptom": f"弦长实测{got_chord:.0f}与目标{want_chord}偏差过大",
                           "suggestion": f"chord -> {want_chord}", "severity": "high"})
        return issues

    fl = spec.get("flange") or {}
    fl_t = float(fl.get("t", 0)) if fl.get("t") else 0.0
    ref_outer = (truth or {}).get("outer") or spec.get("outer", {})
    for key, axis in [("L", "dx"), ("W", "dy"), ("H", "dz")]:
        want = ref_outer.get(key)
        got = size.get(axis)
        if want and got:
            if key == "L" and fl_t: got = got - fl_t
            if abs(want - got) > max(2.0, want * 0.01):
                issues.append({"part": "outer",
                               "symptom": f"外形{key}实测{got:.0f}与真值{want}偏差过大",
                               "suggestion": f"outer.{key} -> {want}", "severity": "high"})
    ref_bores = (truth or {}).get("bores") or spec.get("bores", [])
    spec_bores = {i: b.get("d") for i, b in enumerate(spec.get("bores", []))}
    for i, ref_b in enumerate(ref_bores):
        ref_d = ref_b.get("d") if isinstance(ref_b, dict) else None
        spec_d = spec_bores.get(i)
        if ref_d and spec_d and abs(ref_d - spec_d) > 0.5:
            issues.append({"part": f"bore[{i}]",
                           "symptom": f"bores[{i}].d={spec_d}与真值{ref_d}不符",
                           "suggestion": f"bores[{i}].d -> {ref_d}", "severity": "high"})
    ref_cav = (truth or {}).get("cavities") or []
    spec_cav = spec.get("cavities", [])
    if ref_cav:
        for i, ref_c in enumerate(ref_cav[:len(spec_cav)]):
            ref_w = ref_c.get("w") if isinstance(ref_c, dict) else None
            if ref_w and i < len(spec_cav) and spec_cav[i].get("w") != ref_w:
                issues.append({"part": f"cavity[{i}]",
                               "symptom": f"cavities[{i}].w={spec_cav[i].get('w')}与真值{ref_w}不符",
                               "suggestion": f"cavities[{i}].w -> {ref_w}", "severity": "high"})
    return issues


async def repair_spec(spec: dict, issues: list[dict], family: str) -> dict:
    """参数修正器: 规则建议(高置信) > 案例检索(B) > LLM(A)"""
    # B: 案例检索
    symptom_text = "; ".join(i.get("symptom", "") for i in issues)
    cases = case_library.search(symptom_text, family=family, top_k=3)
    case_hits = [{"id": c["id"], "lesson": c.get("lesson"), "fix": c.get("fix"),
                  "symptom": c.get("symptom")} for c in cases]

    gw = get_gateway()
    if gw.online and issues:
        result = await gw.text_json(REPAIR_SYSTEM, json.dumps({
            "current_params": _flatten_params(spec),
            "issues": issues,
            "case_hits": case_hits,
        }, ensure_ascii=False))
        if result:
            return result
    # 离线降级:
    # 1) 规则类差异自带明确 suggestion ("cavities[0].w -> 190" 或 "outer.W -> 435"),
    #    直接采纳 (确定性最高). 支持嵌套路径 (outer.W) 与数组索引 (bores[0].d).
    import re
    params = {}
    for i in issues:
        sug = i.get("suggestion", "")
        # 形如 "cavities[0].w -> 190" 或 "outer.W -> 435" 或 "bores[1].d -> 200"
        m = re.match(r"^([A-Za-z_][\w\.\[\]\d]*)\s*->\s*([\d.\-]+)$", sug.strip())
        if m:
            params[m.group(1)] = float(m.group(2))
    # 2) 案例库参数建议补充 (症状命中的 param 型 fix)
    if cases:
        for c in cases:
            fix = c.get("fix", {})
            if fix.get("action") == "param":
                for m in re.finditer(r"([A-Za-z_]+)\s*:\s*([\d.]+)\s*->\s*([\d.]+)",
                                     fix.get("detail", "")):
                    params.setdefault(m.group(1), float(m.group(3)))
    return {"params": params, "case_based": bool(cases),
            "cases_used": [c["id"] for c in cases]}


def _flatten_params(spec: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in spec.items():
        key = f"{prefix}{k}"
        if isinstance(v, (int, float)): out[key] = v
        elif isinstance(v, dict): out.update(_flatten_params(v, key + "."))
    return out


def apply_params(spec: dict, params: dict) -> dict:
    """把嵌套路径 {"outer.W": 435, "cavities[0].w": 190, "bores[1].d": 200} 应用回 spec.
    支持点路径 + [N] 数组索引. 缺失父节点自动创建."""
    import copy, re
    spec = copy.deepcopy(spec)
    for path, val in params.items():
        # 解析 path: 拆出 ["outer", "W"] 或 ["cavities", "[0]", "w"]
        tokens = re.findall(r'([A-Za-z_][\w]*)|\[(\d+)\]', path)
        keys = [a or b for a, b in tokens]
        node = spec
        for k in keys[:-1]:
            if re.match(r'^\d+$', str(k)):
                idx = int(k)
                while len(node) <= idx: node.append({})
                node = node[idx]
            else:
                if k not in node or not isinstance(node[k], (dict, list)):
                    node[k] = {}
                node = node[k]
        last = keys[-1]
        if re.match(r'^\d+$', str(last)):
            idx = int(last); node_idx = keys[-2] if len(keys) >= 2 else None
            # 走 list 路径
            if node_idx is not None and node_idx in node:
                target_list = node[node_idx]
                while len(target_list) <= idx: target_list.append({})
                cur = target_list[idx].get(last) if isinstance(target_list[idx], dict) else None
                try: target_list[idx][last] = type(cur)(val) if cur is not None else val
                except Exception: target_list[idx][last] = val
            continue
        # 普通 dict 路径
        try: node[last] = type(node.get(last, val))(val)
        except Exception: node[last] = val
    return spec


def should_stop(score: float | None, round_idx: int, max_rounds: int = 4,
                threshold: float = 0.8, n_issues: int | None = None) -> tuple[bool, str]:
    """收敛判定 (对齐用户要求): 分数>=阈值 且 无遗留问题 才允许收敛交付.
    分数达标但仍有遗留问题时不停, 继续迭代消除; 分数未达标也不停.
    轮次上限不在此处熔断——由调用方的可扩展预算 (基础预算 -> 硬上限) 控制,
    绝不因"到轮次上限"就带着低分/遗留问题草草交付."""
    if score is not None and score >= threshold:
        if not n_issues:
            return True, f"score {score:.2f}≥{threshold} 且无遗留问题, 收敛"
        return False, f"分数达标 ({score:.2f}) 但仍有 {n_issues} 项遗留问题"
    return False, f"score {score} 未达阈值 {threshold}"
