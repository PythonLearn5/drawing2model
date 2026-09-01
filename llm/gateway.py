# -*- coding: utf-8 -*-
"""
LLM Gateway — 所有模型调用收口, 通过环境变量配置
- LLM_API_KEY: API 凭证 (默认无 -> 离线模式)
- LLM_BASE_URL: OpenAI 兼容 endpoint (默认阿里云 DashScope)
- LLM_MODEL:   通用模型 (同时用于视觉+文本; 默认 qwen-vl-max)
- LLM_VL_MODEL / LLM_TEXT_MODEL: 可选, 单独覆盖视觉/文本模型
- 兼容回退: DASHSCOPE_API_KEY 可作 LLM_API_KEY 的别名 (老配置不破坏)
- 离线降级: 无 Key 或调用失败, 走规则/本地逻辑, 流水线仍可跑
用法: LLMGateway() 自动探测环境
"""
from __future__ import annotations
import json, os, base64, hashlib, threading
from pathlib import Path

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-max"   # 默认模型: 同时支持视觉和文本 (OpenAI 兼容多模态接口)
CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / ".llm_cache"


def _mime(b: bytes) -> str:
    """按魔数判定图像 MIME (高清版可能是 JPEG, 不能硬编码 png)."""
    if b.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if b.startswith(b"\x89PNG"):
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


# ---- token 用量统计 (线程本地: 每个任务线程独立累计) ----
_tls = threading.local()


def usage_reset() -> None:
    """重置当前线程的 token 累计 (任务开始时调用)."""
    _tls.tokens = {"prompt": 0, "completion": 0, "calls": 0}


def usage_since() -> dict:
    """返回当前线程累计用量并清零, 用于按轮次统计."""
    t = getattr(_tls, "tokens", None) or {"prompt": 0, "completion": 0, "calls": 0}
    usage_reset()
    return dict(t)


def usage_total() -> dict:
    """返回当前线程累计用量 (不清零), 任务结束时取总量."""
    t = getattr(_tls, "tokens", None) or {"prompt": 0, "completion": 0, "calls": 0}
    return dict(t)


def _usage_add(resp) -> None:
    """从 OpenAI 响应累加 usage (缓存命中不计费, 由调用方决定)."""
    try:
        u = getattr(resp, "usage", None)
        if not u:
            return
        if not hasattr(_tls, "tokens"):
            usage_reset()
        _tls.tokens["prompt"] += int(getattr(u, "prompt_tokens", 0) or 0)
        _tls.tokens["completion"] += int(getattr(u, "completion_tokens", 0) or 0)
        _tls.tokens["calls"] += 1
    except Exception:
        pass


def _resolve_config() -> dict:
    """从环境变量解析 LLM 配置 (优先级: 通用 > DashScope 兼容别名)"""
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")   # 向后兼容
        or ""
    ).strip()
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).strip()
    # 视觉模型: 单独覆盖 > 通用 > 默认
    model_vl = (
        os.environ.get("LLM_VL_MODEL")
        or os.environ.get("LLM_MODEL")
        or DEFAULT_MODEL
    ).strip()
    # 文本模型: 单独覆盖 > 通用 > 默认 (Qwen 系列文本模型也支持多模态, 故可同视觉)
    model_text = (
        os.environ.get("LLM_TEXT_MODEL")
        or os.environ.get("LLM_MODEL")
        or DEFAULT_MODEL
    ).strip()
    return {"api_key": api_key, "base_url": base_url,
            "model_vl": model_vl, "model_text": model_text}


def _cache_key(images: list[bytes], system: str, user: str) -> str:
    h = hashlib.sha256()
    for b in images: h.update(b)
    h.update(system.encode()); h.update(user.encode())
    return h.hexdigest()


class LLMGateway:
    def __init__(self, config: dict | None = None):
        cfg = config or _resolve_config()
        self.api_key = cfg["api_key"]
        self.base_url = cfg["base_url"]
        self.model_vl = cfg["model_vl"]
        self.model_text = cfg["model_text"]
        self.online = bool(self.api_key) and AsyncOpenAI is not None
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url) if self.online else None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 基础调用 ----------
    async def vision_json(self, images: list[bytes], system: str, user: str) -> dict | None:
        """图像(+文字) -> JSON. 离线或失败返回 None, 由调用方降级."""
        if not self.online:
            return self._offline_vision(images, system, user)
        key = _cache_key(images, system, user)
        cache = CACHE_DIR / f"{key}.json"
        if cache.exists():
            try: return json.loads(cache.read_text("utf-8"))
            except Exception: pass
        content = [{"type": "image_url", "image_url": {"url": "data:" + _mime(b)
                   + ";base64," + base64.b64encode(b).decode()}} for b in images]
        content.append({"type": "text", "text": user})
        txt = await self._chat(self.model_vl, system, content)
        if txt is None:
            return self._offline_vision(images, system, user)
        obj = _extract_json(txt)
        if obj is None:
            print(f"[gateway] vision_json JSON 提取失败, 原始输出前 300 字: {txt[:300]!r}", flush=True)
        else:
            cache.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
        return obj

    async def _chat(self, model: str, system: str, content, attempts: int = 2) -> str | None:
        """带重试+日志的底层调用; 返回原始文本或 None."""
        last = None
        for i in range(attempts):
            try:
                resp = await self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": content}],
                    temperature=0.1)
                _usage_add(resp)
                txt = resp.choices[0].message.content or ""
                if txt.strip():
                    return txt
                last = "空回复 (可能输出在 reasoning_content)"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            print(f"[gateway] 调用失败(第{i+1}/{attempts}次) model={model}: {last}", flush=True)
        return None

    async def text_json(self, system: str, user: str) -> dict | None:
        if not self.online:
            return None
        key = _cache_key([], system, user)
        cache = CACHE_DIR / f"{key}.json"
        if cache.exists():
            try: return json.loads(cache.read_text("utf-8"))
            except Exception: pass
        txt = await self._chat(self.model_text, system, user)
        if txt is None:
            return None
        obj = _extract_json(txt)
        if obj is not None:
            cache.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
        return obj

    # ---------- 离线降级 ----------
    def _offline_vision(self, images: list[bytes], system: str, user: str) -> dict | None:
        """离线: 无 VLM, 返回 None 让调用方走规则路径 (不能假装识别)"""
        return None

    def status(self) -> dict:
        return {
            "mode": "online" if self.online else "offline",
            "vl_model": self.model_vl if self.online else None,
            "text_model": self.model_text if self.online else None,
        }


def _extract_json(txt: str) -> dict | None:
    """从模型输出提取 JSON: 优先 ```json 块, 再找首尾大括号"""
    txt = txt.strip()
    if txt.startswith("```"):
        parts = txt.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"): p = p[4:].strip()
            if p.startswith("{"):
                try: return json.loads(p)
                except Exception: continue
    for i, ch in enumerate(txt):
        if ch == "{":
            for j in range(len(txt), i, -1):
                if txt[j-1] == "}":
                    try: return json.loads(txt[i:j])
                    except Exception: continue
    return None


# 模块级单例
_gateway: LLMGateway | None = None
def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
