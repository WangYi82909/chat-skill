"""
imessage.py
消息处理中枢：
- 接收来自 websocket_qq.py 的原始群消息
- 判断是否需要回复（关键词匹配）
- 调用 main.py 的 process_input() 进行思考
- 拦截异常格式输出
- 过滤文本（【】等标记）
- 分段发送长消息
- 主动发言逻辑
"""

import re
import time
import asyncio
import yaml
import os
from datetime import datetime

# ================= 引入 main.py 核心逻辑 =================
from main import process_input, log, C, CONFIG, print_separator

# ================= 引入插件系统 =================
import plugin

# ================= 模块名（用于日志标记）=================
MOD = "MSG"

# ================= 对话历史（群维度，按 group_id 隔离）=================
_conv_histories: dict[str, list] = {}

def get_history(group_id: str) -> list:
    if group_id not in _conv_histories:
        _conv_histories[group_id] = []
    return _conv_histories[group_id]

def push_history(group_id: str, role: str, content: str):
    history = get_history(group_id)
    history.append({"role": role, "content": content})
    max_len = CONFIG.get("max_history_turns", 20) * 2  # 每轮 user+assistant
    if len(history) > max_len:
        # 保留最近 N 轮
        _conv_histories[group_id] = history[-max_len:]

# ================= 关键词匹配 =================

def should_reply(text: str) -> bool:
    """判断消息是否需要触发回复"""
    keywords: list = CONFIG.get("reply_keywords", ["梦梦", "小梦"])
    for kw in keywords:
        if kw in text:
            return True
    return False

# ================= 文本过滤 =================

def filter_text(text: str) -> str:
    """
    过滤输出文本中的干扰内容：
    - 去除 【...】 标记
    - 去除多余空白行
    - 可在 config.yaml 的 filter_patterns 中扩展正则
    """
    # 去除【...】全角书名号标记
    text = re.sub(r'【[^】]{0,30}】', '', text)
    # 去除自定义过滤规则
    for pattern in CONFIG.get("filter_patterns", []):
        text = re.sub(pattern, '', text)
    # 合并多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ================= 异常格式拦截 =================

def is_blocked(text: str) -> bool:
    """
    检查回复是否包含不应发送的内容：
    - JSON 代码块 ```json ... ```
    - 反引号代码块
    - 裸 { } 结构（疑似工具调用泄露）
    - 可在 config.yaml 的 block_patterns 中扩展
    """
    block_patterns = CONFIG.get("block_patterns", [
        r'```',
        r'\{[\s\S]{0,200}"action"\s*:',
    ])
    for pattern in block_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# ================= 分段发送 =================

def split_message(text: str) -> list[str]:
    """
    将长文本按 max_segment_length 分段。
    优先在句尾（。！？\n）处截断，避免断句。
    """
    max_len: int = CONFIG.get("max_segment_length", 200)
    if len(text) <= max_len:
        return [text]

    segments = []
    while len(text) > max_len:
        # 在 max_len 范围内寻找最后一个自然断句位置
        chunk = text[:max_len]
        cut = max(
            chunk.rfind('。'),
            chunk.rfind('！'),
            chunk.rfind('？'),
            chunk.rfind('…'),
            chunk.rfind('\n'),
        )
        if cut <= 0:
            cut = max_len  # 找不到断句则硬截
        else:
            cut += 1       # 包含标点
        segments.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        segments.append(text)
    return segments

# ================= 发送函数（注入自 websocket_qq）=================

# 由 websocket_qq.py 在启动时注入此函数
_send_group_message = None

def register_send_func(func):
    """websocket_qq.py 调用此函数注册发送接口"""
    global _send_group_message
    _send_group_message = func

async def send_text(group_id: str, text: str, reply_msg_id=None):
    """
    发送一条文本消息到群组。
    reply_msg_id 不为 None 时追加引用回复 segment。
    """
    if _send_group_message is None:
        log("WARN", "发送函数未注册，消息无法发出", MOD)
        return

    msg_array = []
    if reply_msg_id:
        msg_array.append({"type": "reply", "data": {"id": str(reply_msg_id)}})
    msg_array.append({"type": "text", "data": {"text": text}})

    await _send_group_message(group_id, msg_array)

async def send_segments(group_id: str, text: str, reply_msg_id=None):
    """分段发送，每段之间间隔 segment_send_interval 秒"""
    segments = split_message(text)
    interval = CONFIG.get("segment_send_interval", 0.8)

    for i, seg in enumerate(segments):
        # 只在第一段携带引用
        rid = reply_msg_id if i == 0 else None
        await send_text(group_id, seg, reply_msg_id=rid)
        log("MSG", f"已发送第 {i+1}/{len(segments)} 段（{len(seg)} 字）", MOD)
        if i < len(segments) - 1:
            await asyncio.sleep(interval)

# ================= 主处理入口 =================

async def handle_message(data: dict):
    """
    由 websocket_qq.py 接收到消息后调用。
    data 为 NapCat/go-cqhttp 格式的事件字典。
    """
    if data.get("post_type") != "message":
        return

    raw_text: str  = data.get("raw_message", "")
    group_id: str  = str(data.get("group_id", ""))
    msg_id         = data.get("message_id")
    sender         = data.get("sender", {})
    nickname: str  = sender.get("nickname", "用户")

    # 过滤 CQ 码中的非文本内容（文件、图片等），只保留纯文本部分
    plain_text = re.sub(r'\[CQ:[^\]]+\]', '', raw_text).strip()

    log("MSG", f"[{group_id}] {nickname}: {plain_text[:80]}", MOD)

    if not plain_text:
        return

    # ---- 优先检查插件指令（无需关键词触发）----
    cmd_reply = plugin.handle_command(plain_text)
    if cmd_reply is not None:
        log("INFO", "指令命中，直接回复", MOD)
        await send_text(group_id, cmd_reply, reply_msg_id=msg_id)
        return

    # ---- 关键词检测，决定是否触发 AI ----
    if not should_reply(plain_text):
        return

    log("INFO", f"命中关键词，开始思考回复...", MOD)

    # 更新对话历史
    push_history(group_id, "user", f"{nickname}: {plain_text}")

    history = get_history(group_id)

    # ---- 调用 main.py 思考 ----
    try:
        reply = await asyncio.get_event_loop().run_in_executor(
            None, process_input, plain_text, history
        )
    except Exception as e:
        log("WARN", f"process_input 异常: {e}", MOD)
        await send_text(group_id, "【系统异常】思考过程出错，请稍后再试", reply_msg_id=msg_id)
        return

    # ---- 异常格式拦截 ----
    if is_blocked(reply):
        log("WARN", f"回复被拦截（含异常格式）: {reply[:80]}", MOD)
        await send_text(group_id, "【未知错误】imessage被拦截", reply_msg_id=msg_id)
        return

    # ---- 文本过滤 ----
    reply = filter_text(reply)

    if not reply:
        log("WARN", "过滤后回复为空，跳过发送", MOD)
        return

    # ---- 更新历史 & 分段发送 ----
    push_history(group_id, "assistant", reply)

    bot_name = CONFIG.get("bot_name", "AI")
    log("MSG", f"{bot_name} 回复（前80字）: {reply[:80]}", MOD)

    await send_segments(group_id, reply, reply_msg_id=msg_id)

# ================= 主动发言逻辑 =================

_last_active_time: dict[str, float] = {}

async def active_speak_loop(group_id: str):
    """
    定时主动发言。
    发言内容由 active_speak_prompt 配置决定，经 process_input 思考后发送。
    """
    interval = CONFIG.get("active_interval_seconds", 1800)
    prompt   = CONFIG.get("active_speak_prompt", "你可以主动说点什么")

    log("INFO", f"主动发言任务启动，间隔 {interval}s，群: {group_id}", MOD)

    while True:
        await asyncio.sleep(interval)
        log("INFO", f"触发主动发言 -> 群 {group_id}", MOD)

        history = get_history(group_id)
        try:
            reply = await asyncio.get_event_loop().run_in_executor(
                None, process_input, prompt, history
            )
        except Exception as e:
            log("WARN", f"主动发言 process_input 异常: {e}", MOD)
            continue

        if is_blocked(reply):
            log("WARN", f"主动发言被拦截", MOD)
            continue

        reply = filter_text(reply)
        if not reply:
            continue

        push_history(group_id, "assistant", reply)
        await send_segments(group_id, reply)
