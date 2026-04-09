"""
plugin.py
指令插件系统
- 在 imessage.py 的 handle_message 中被调用
- 命中指令则处理并返回回复文本，不命中返回 None（交给 AI 处理）

当前指令：
  /meng token  → 查询 data/tokens.json 中的 Token 消耗统计
"""

import json
import os
from main import CONFIG, get_abs_path, log

MOD = "PLUGIN"

# ================= 指令前缀 =================

COMMAND_PREFIX = CONFIG.get("command_prefix", "/meng")

# ================= 工具函数 =================

def _load_tokens() -> dict | None:
    file_path = os.path.join(get_abs_path("data"), "tokens.json")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("WARN", f"读取 tokens.json 失败: {e}", MOD)
        return None

# ================= 指令处理器 =================

def _cmd_token() -> str:
    data = _load_tokens()
    if data is None:
        return "暂无 Token 统计数据（data/tokens.json 不存在）"

    stats = data.get("total_stats", {})
    prompt_t     = stats.get("prompt_tokens", 0)
    completion_t = stats.get("completion_tokens", 0)
    total_t      = stats.get("total_tokens", 0)

    history      = data.get("history", [])
    call_count   = len(history)

    # 最近一次调用信息
    last_info = ""
    if history:
        last = history[-1]
        last_info = f"\n上次调用：{last.get('timestamp', '?')}  共 {last.get('total', 0)} tokens"

    return (
        f"Token 消耗统计\n"
        f"━━━━━━━━━━━━━━\n"
        f"输入（prompt）  ：{prompt_t:,}\n"
        f"输出（completion）：{completion_t:,}\n"
        f"总计（total）   ：{total_t:,}\n"
        f"累计调用次数    ：{call_count} 次"
        f"{last_info}"
    )

# ================= 指令分发 =================

# 指令表：子命令 → 处理函数（无参数版）
_COMMANDS: dict = {
    "token": _cmd_token,
}

def handle_command(text: str) -> str | None:
    """
    检查消息是否为指令。
    是指令 → 返回回复字符串
    不是指令 → 返回 None
    """
    stripped = text.strip()

    if not stripped.startswith(COMMAND_PREFIX):
        return None

    # 去掉前缀，取子命令
    rest = stripped[len(COMMAND_PREFIX):].strip().lower()

    if not rest:
        # 只输入了 /meng，返回帮助
        cmds = "、".join(f"{COMMAND_PREFIX} {k}" for k in _COMMANDS)
        return f"指令系统\n可用指令：{cmds}"

    handler = _COMMANDS.get(rest)
    if handler is None:
        return f"未知指令：{COMMAND_PREFIX} {rest}\n可用：{', '.join(_COMMANDS.keys())}"

    log("INFO", f"执行指令: {COMMAND_PREFIX} {rest}", MOD)
    try:
        return handler()
    except Exception as e:
        log("WARN", f"指令执行异常: {e}", MOD)
        return f"指令执行出错：{e}"
