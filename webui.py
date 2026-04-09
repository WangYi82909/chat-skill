"""
webui.py
Flask 聊天 Web 界面服务端
目录结构：
  webui.py          ← 根目录运行
  templates/
    chat.html       ← 主页面
    settings.html   ← 设置页
  icons/            ← 原始图标

路由：
  GET  /                → chat.html
  GET  /settings        → settings.html
  GET  /config          → 界面配置 JSON
  GET  /history         → 历史记录 JSON（含分段列表）
  POST /clear           → 清空历史
  POST /send            → SSE 分段回复
  GET  /yaml_config     → 读取 config.yaml 所有字段
  POST /yaml_config     → 写入 config.yaml 字段
"""

import json
import os
import re
import time
import threading

import yaml
from flask import (
    Flask, Response, jsonify, render_template,
    request, send_from_directory, stream_with_context
)

from main import process_input, log, get_abs_path

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(BASE_DIR, "config.yaml")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=BASE_DIR, static_url_path="")
MOD = "WEBUI"

# ==================== 配置热加载 ====================

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_config(data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

CONFIG = load_config()

# ==================== 会话历史 ====================
# 历史以"消息对象"列表保存，每条含 role / content / segments（分段列表，仅 assistant 有）

_history: list[dict] = []
_history_lock = threading.Lock()
_HISTORY_PATH = os.path.join(BASE_DIR, "data", "webui_history.json")


def _load_history():
    if os.path.exists(_HISTORY_PATH):
        try:
            with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_history():
    os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
    with open(_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(_history, f, ensure_ascii=False, indent=2)


_history = _load_history()

# ==================== 分段逻辑 ====================

_STRIP_PUNCT = re.compile(r'[，。！？!?,、…]+$')


def split_reply(text: str) -> list[str]:
    cfg = load_config()
    split_chars = cfg.get("webui_split_chars", "，。！？!?,、…\n")
    max_len     = cfg.get("webui_segment_max_len", 80)

    segments, buf = [], ""
    for ch in text:
        buf += ch
        if ch in split_chars and buf.strip():
            seg = _STRIP_PUNCT.sub("", buf.strip())
            if seg:
                segments.append(seg)
            buf = ""
        elif len(buf) >= max_len:
            seg = _STRIP_PUNCT.sub("", buf.strip())
            if seg:
                segments.append(seg)
            buf = ""
    if buf.strip():
        seg = _STRIP_PUNCT.sub("", buf.strip())
        if seg:
            segments.append(seg)

    return segments

# ==================== 路由 ====================

@app.route("/")
def index():
    return render_template("chat.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/config")
def get_config():
    cfg = load_config()
    return jsonify({
        "bot_name":   cfg.get("bot_name", "梦梦"),
        "user_name":  cfg.get("webui_user_name", "我"),
        "bot_avatar": cfg.get("webui_bot_avatar", "icons/index/default_bot.png"),
        "user_avatar": cfg.get("webui_user_avatar", "icons/index/default_user.png"),
        "chat_title": cfg.get("bot_name", "梦梦"),
    })


@app.route("/history")
def get_history():
    with _history_lock:
        return jsonify(_history)


@app.route("/clear", methods=["POST"])
def clear_history():
    with _history_lock:
        _history.clear()
        _save_history()
    return jsonify({"ok": True})


@app.route("/send", methods=["POST"])
def send_message():
    body      = request.get_json(force=True, silent=True) or {}
    user_text = (body.get("text") or "").strip()
    if not user_text:
        return jsonify({"error": "empty"}), 400

    with _history_lock:
        _history.append({"role": "user", "content": user_text, "segments": [user_text]})
        # process_input 需要纯 role/content 格式
        history_snapshot = [{"role": h["role"], "content": h["content"]} for h in _history]

    log("INFO", f"WebUI 收到: {user_text[:60]}", MOD)

    def generate():
        try:
            reply    = process_input(user_text, history_snapshot)
            segments = split_reply(reply)
            total    = len(segments)
            cfg      = load_config()
            interval = cfg.get("webui_segment_interval", 0.6)

            for i, seg in enumerate(segments):
                yield f"data: {json.dumps({'type':'segment','text':seg,'index':i,'total':total}, ensure_ascii=False)}\n\n"
                if i < total - 1:
                    time.sleep(interval)

            with _history_lock:
                _history.append({
                    "role":     "assistant",
                    "content":  reply,
                    "segments": segments      # ← 保存分段，刷新后原样还原
                })
                _save_history()

            yield 'data: {"type":"done"}\n\n'

        except Exception as e:
            log("WARN", f"WebUI 异常: {e}", MOD)
            yield f'data: {json.dumps({"type":"error","msg":str(e)})}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ==================== YAML 配置 API ====================

@app.route("/yaml_config", methods=["GET"])
def yaml_get():
    return jsonify(load_config())


@app.route("/yaml_config", methods=["POST"])
def yaml_set():
    updates = request.get_json(force=True, silent=True) or {}
    cfg = load_config()

    def cast(old_val, new_val):
        """尽量保留原始类型"""
        if isinstance(old_val, bool):
            return str(new_val).lower() in ("true", "1", "yes")
        if isinstance(old_val, int):
            try: return int(new_val)
            except: return old_val
        if isinstance(old_val, float):
            try: return float(new_val)
            except: return old_val
        if isinstance(old_val, list):
            # 逗号分隔字符串 → list
            if isinstance(new_val, list):
                return new_val
            return [v.strip() for v in str(new_val).split(",") if v.strip()]
        return new_val

    for k, v in updates.items():
        if k in cfg:
            cfg[k] = cast(cfg[k], v)
        else:
            cfg[k] = v

    save_config(cfg)
    log("INFO", f"config.yaml 已更新 {list(updates.keys())}", MOD)
    return jsonify({"ok": True})


# ==================== 入口 ====================

if __name__ == "__main__":
    cfg  = load_config()
    host = cfg.get("webui_host", "0.0.0.0")
    port = cfg.get("webui_port", 5000)
    log("SYSTEM", f"WebUI 启动 → http://{host}:{port}", MOD)
    app.run(host=host, port=port, debug=cfg.get("webui_debug", False), threaded=True)
