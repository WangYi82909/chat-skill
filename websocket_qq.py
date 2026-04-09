"""
websocket_qq.py
WebSocket 连接层（兼容 websockets 14.x / 16.x 新 asyncio API）
- token 已在 URL query string 中，不传任何自定义 headers
- 接收消息 → 本地存档 + 转发 imessage.py 自动处理
- 手动发送：直接输入 / r+空格 引用回复
"""

import asyncio
import json
import os
import sys
import time

from websockets.asyncio.client import connect

from main import log, C, CONFIG
import imessage

MOD = "WS"

# ================= 配置 =================

WS_BASE  = CONFIG["ws_base"]
WS_TOKEN = CONFIG["ws_token"]
WS_URL   = f"{WS_BASE}?access_token={WS_TOKEN}"
GROUP_ID = str(CONFIG.get("group_id", ""))

# ================= 全局状态 =================

_ws_conn     = None   # 当前连接对象
_last_msg_id = None   # 最近消息 ID（引用回复用）
_current_gid = GROUP_ID

# ================= 本地存档 =================

def save_message_local(data: dict):
    if data.get("post_type") != "message":
        return

    log_dir = CONFIG.get("chat_log_dir", "chat_log")
    os.makedirs(log_dir, exist_ok=True)

    date_str  = time.strftime("%Y-%m-%d", time.localtime())
    file_path = os.path.join(log_dir, f"{date_str}.jsonl")

    record = {
        "id":   data.get("message_id"),
        "time": time.strftime("%H:%M:%S", time.localtime(data.get("time", time.time()))),
        "name": data.get("sender", {}).get("nickname", "未知"),
        "text": data.get("raw_message", "")
    }

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ================= 发送接口（注册给 imessage）=================

async def send_group_message(group_id: str, msg_array: list):
    if _ws_conn is None:
        log("WARN", "WS 连接未建立，无法发送消息", MOD)
        return
    payload = {
        "action": "send_group_msg",
        "params": {"group_id": int(group_id), "message": msg_array}
    }
    try:
        await _ws_conn.send(json.dumps(payload, ensure_ascii=False))
        log("WS", f"已发送消息到群 {group_id}", MOD)
    except Exception as e:
        log("WARN", f"发送失败: {e}", MOD)

# ================= 接收协程 =================

async def receive_handler(ws):
    global _ws_conn, _last_msg_id, _current_gid
    _ws_conn = ws
    log("WS", f"连接成功 → {WS_URL[:60]}", MOD)

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log("WARN", f"JSON 解析失败: {raw[:60]}", MOD)
                continue

            post_type = data.get("post_type", "")

            if post_type == "meta_event":
                continue   # 静默过滤心跳

            if post_type == "message":
                _last_msg_id = data.get("message_id")
                if data.get("group_id"):
                    _current_gid = str(data["group_id"])

                save_message_local(data)

                nickname = data.get("sender", {}).get("nickname") or "用户"
                content  = data.get("raw_message", "")
                sys.stdout.write(
                    f"\r\033[K{C.BLUE}[{nickname}]{C.RESET}: {content}\n发送(r回复/enter跳过): "
                )
                sys.stdout.flush()

                asyncio.create_task(imessage.handle_message(data))

            elif post_type == "notice":
                log("WS", f"通知: {data.get('notice_type', '?')}", MOD)

    except Exception as e:
        log("WARN", f"接收异常: {e}", MOD)
    finally:
        _ws_conn = None

# ================= 手动发送协程 =================

async def send_handler():
    log("WS", "手动发送已启动 | 直接输入发送 | r+空格 引用回复 | exit 退出", MOD)
    loop = asyncio.get_event_loop()

    while True:
        sys.stdout.write("发送(r回复/enter跳过): ")
        sys.stdout.flush()

        user_input = await loop.run_in_executor(None, sys.stdin.readline)
        user_input = user_input.strip()

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            log("SYSTEM", "手动退出", MOD)
            os._exit(0)

        msg_array = []
        if user_input.startswith("r ") and _last_msg_id:
            msg_array.append({"type": "reply", "data": {"id": str(_last_msg_id)}})
            msg_array.append({"type": "text",  "data": {"text": user_input[2:].strip()}})
        else:
            msg_array.append({"type": "text", "data": {"text": user_input}})

        if _ws_conn is None:
            log("WARN", "连接已断开，发送失败", MOD)
            continue

        try:
            payload = {
                "action": "send_group_msg",
                "params": {"group_id": int(_current_gid), "message": msg_array}
            }
            await _ws_conn.send(json.dumps(payload, ensure_ascii=False))
            log("WS", f"手动发送 → 群 {_current_gid}", MOD)
        except Exception as e:
            log("WARN", f"手动发送失败: {e}", MOD)

# ================= 主动发言任务 =================

def start_active_tasks():
    groups = CONFIG.get("active_groups", [GROUP_ID])
    for gid in groups:
        if gid:
            asyncio.create_task(imessage.active_speak_loop(str(gid)))
            log("WS", f"主动发言任务已启动 → 群 {gid}", MOD)

# ================= 连接主循环 =================

async def main():
    imessage.register_send_func(send_group_message)

    reconnect_delay = CONFIG.get("ws_reconnect_delay", 5)
    max_retries     = CONFIG.get("ws_max_retries", 0)
    retry_count     = 0
    
    os.system("toilet -f big --gay Webchat-Agent")
    print("\033[1;35mAgent：为构建一个最拟人化的思考流而奋斗\033[0m")


    print(f"\n{C.BOLD}{C.MAGENTA}{'=' * 54}{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}   WebSocket 模式{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}{'=' * 54}{C.RESET}")
    print(f"{C.GREY}  WS 地址 : {WS_URL[:60]}{C.RESET}")
    print(f"{C.GREY}  默认群组: {GROUP_ID}{C.RESET}")
    print(f"{C.GREY}  关键词  : {CONFIG.get('reply_keywords', [])}{C.RESET}\n")

    send_task = asyncio.create_task(send_handler())

    while True:
        log("WS", f"正在连接 {WS_URL[:60]} ...", MOD)
        try:
            # websockets 16.x 新 API：connect() 不传 headers，token 在 URL 里
            async with connect(WS_URL) as ws:
                retry_count = 0
                log("WS", "WebSocket 连接成功", MOD)
                start_active_tasks()
                await receive_handler(ws)

        except OSError as e:
            retry_count += 1
            log("WARN", f"连接失败 ({retry_count}): {e}", MOD)
        except Exception as e:
            retry_count += 1
            log("WARN", f"未知异常 ({retry_count}): {e}", MOD)

        if max_retries > 0 and retry_count >= max_retries:
            log("WARN", f"已达最大重连次数 {max_retries}，退出", MOD)
            send_task.cancel()
            break

        log("WS", f"{reconnect_delay}s 后重连...", MOD)
        await asyncio.sleep(reconnect_delay)

# ================= 入口 =================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("SYSTEM", "已手动退出", MOD)
