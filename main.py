import os
import json
import time
import re
import requests
import subprocess
import yaml
from datetime import datetime

# ================= 加载 YAML 配置 =================

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

CONFIG = load_config()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_abs_path(rel_path):
    return os.path.normpath(os.path.join(BASE_DIR, rel_path))

# ================= 控制台颜色 =================

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    GREY    = "\033[90m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    WHITE   = "\033[97m"

def log(level, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    colors = {
        "INFO":   C.CYAN,
        "TOOL":   C.YELLOW,
        "WARN":   C.RED,
        "SYSTEM": C.MAGENTA,
        "CACHE":  C.BLUE,
        "USER":   C.WHITE,
    }
    color = colors.get(level, C.RESET)
    print(f"{C.GREY}[{ts}]{C.RESET} {color}[{level}]{C.RESET} {msg}")

def print_separator(title=""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"{C.GREY}{'─' * pad} {C.BOLD}{title}{C.RESET}{C.GREY} {'─' * (width - pad - len(title) - 2)}{C.RESET}")
    else:
        print(f"{C.GREY}{'─' * width}{C.RESET}")

def print_cache(round_log):
    if not round_log:
        return
    log("CACHE", f"本轮已执行操作（共 {len(round_log)} 步）：")
    for i, r in enumerate(round_log, 1):
        result_preview = r["result"][:100] if r["result"] else "人格已更新（correction）"
        log("CACHE", f"  {i}. [{r['action']}] 理由: {r['why']}")
        log("CACHE", f"     └─ 结果: {result_preview}")

# ================= 文件读取 =================

def load_file(path_key):
    """每次调用都实时从磁盘读取文件"""
    p = get_abs_path(CONFIG[path_key])
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    log("WARN", f"文件不存在: {p}")
    return ""

# ================= 日志 =================

def log_round(timestamp, messages, response_text):
    log_dir = get_abs_path(CONFIG["log_dir"])
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{timestamp}.json")
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "request": messages,
            "response": response_text
        }, f, ensure_ascii=False, indent=4)
    log("INFO", f"本轮日志已写入: logs/{timestamp}.json")

# ================= 工具执行 =================

def run_tool(script_key, *args):
    script_path = get_abs_path(CONFIG[script_key])
    try:
        result = subprocess.check_output(
            ["python3", script_path] + list(args),
            stderr=subprocess.STDOUT,
            encoding="utf-8"
        )
        return result.strip()
    except subprocess.CalledProcessError as e:
        return f"[工具错误] {e.output.strip()}"
    except Exception as e:
        return f"[工具异常] {str(e)}"

def dispatch_tool(cmd_data):
    """根据 action 调用对应脚本；correction 返回 None"""
    action = cmd_data.get("action", "")
    why    = cmd_data.get("why?", "")

    if action == "query":
        keyword = cmd_data.get("keyword", "")
        log("TOOL", f"▶ 执行 query")
        log("TOOL", f"  理由: {why}")
        log("TOOL", f"  检索关键词: 「{keyword}」")
        result = run_tool("query_script", keyword)
        log("TOOL", f"  完成，返回 {len(result)} 字符")
        return result

    elif action == "search":
        keyword = cmd_data.get("keyword", "")
        log("TOOL", f"▶ 执行 search")
        log("TOOL", f"  理由: {why}")
        log("TOOL", f"  关键词: 「{keyword}」")
        result = run_tool("search_script", keyword)
        log("TOOL", f"  完成，返回 {len(result)} 字符")
        return result

    elif action == "correction":
        behavior = cmd_data.get("行为标签", "")
        emotion  = cmd_data.get("情绪标签", "")
        log("TOOL", f"▶ 执行 correction（人格重建）")
        log("TOOL", f"  理由: {why}")
        log("TOOL", f"  行为标签: {behavior} | 情绪标签: {emotion}")
        result = run_tool("correction_script", behavior, emotion)
        log("TOOL", f"  完成: {result}")
        return None  # 特殊标记：触发重新注入 system prompt

    else:
        log("WARN", f"未知 action: {action}")
        return f"[未知工具 action: {action}]"

# ================= JSON 清洗 =================

def extract_json(text):
    text = re.sub(r'```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```\s*', '', text)
    match = re.search(r'(\{.*?\})', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def is_tool_call(text):
    cleaned = extract_json(text)
    if not cleaned:
        return False, None
    try:
        data = json.loads(cleaned)
        if "action" in data:
            return True, data
    except Exception:
        pass
    return False, None

# ================= LLM 请求 =================

def call_llm(messages):
    ts = int(time.time() * 1000)
    log("INFO", "请求 LLM 中...")
    response = requests.post(
        CONFIG["llm_endpoint"],
        headers={"Authorization": f"Bearer {CONFIG['llm_key']}"},
        json={
            "model":       CONFIG["llm_model"],
            "messages":    messages,
            "temperature": CONFIG.get("llm_temperature", 0.8)
        }
    )
    data = response.json()
    reply = data["choices"][0]["message"]["content"].strip()
    log_round(ts, messages, reply)
    return reply

# ================= 消息构建 =================

def build_messages(history_str, user_input, round_log, extra_context="", used_tools=None):
    """
    每次调用都实时读取 core.MD 和 drive.MD。
    used_tools: set —— 本轮已用过的工具名，告知 AI 本轮禁止重复使用。
    """
    core_md  = load_file("core_md")
    drive_md = load_file("drive_md")

    # 临时缓存摘要
    cache_summary = ""
    if round_log:
        cache_summary = "\n\n【本轮临时缓存（你本轮已执行的操作，务必记住）】\n" + "\n".join(
            [
                f"- [{r['action']}] 理由: {r['why']} → 结果: {(r['result'][:150] if r['result'] else '人格已更新')}"
                for r in round_log
            ]
        )

    # 工具限制说明
    tool_restriction = ""
    if used_tools:
        tool_restriction = (
            f"\n\n【本轮工具限制 ⚠️】\n"
            f"以下工具本轮已执行过，本次绝对禁止再次调用：{', '.join(used_tools)}\n"
            f"如需相关信息请直接使用上方临时缓存中的结果，不要重复调用工具。"
        )

    user_content = (
        f"【对话历史】\n{history_str}\n\n"
        f"【用户刚刚说】\n用户:{user_input}"
        f"{cache_summary}"
        f"{tool_restriction}"
        f"{extra_context}\n\n"
        f"---\n{drive_md}"
    )

    return [
        {"role": "system", "content": core_md},
        {"role": "user",   "content": user_content}
    ]

# ================= 对话历史格式化 =================

def format_history(history):
    """将对话历史格式化为 name:内容 形式"""
    lines = []
    bot_name = CONFIG.get("bot_name", "AI")
    for entry in history:
        name    = "用户" if entry["role"] == "user" else bot_name
        content = entry["content"]
        lines.append(f"{name}:{content}")
    return "\n".join(lines)

# ================= 核心处理 =================

def process_input(user_input, conversation_history):
    """
    处理一轮用户输入，执行多轮工具调用后返回最终回复。
    conversation_history: list of {"role": "user"/"assistant", "content": "..."}
    """
    print_separator("AI 思考中")
    history_str = format_history(conversation_history)

    round_log   = []        # 本轮临时缓存
    used_tools  = set()     # 本轮已用工具集合
    max_tool_loops = 6
    loop_count  = 0
    final_reply = None
    extra_context = ""

    while loop_count < max_tool_loops:
        loop_count += 1
        log("INFO", f"第 {loop_count} 轮推理（实时读取 core.MD / drive.MD）")

        # 每轮开始展示当前缓存
        if round_log:
            print_cache(round_log)
        if used_tools:
            log("INFO", f"本轮已用工具（禁止重复）: {', '.join(used_tools)}")

        messages = build_messages(
            history_str, user_input, round_log,
            extra_context, used_tools
        )
        reply = call_llm(messages)
        log("INFO", f"LLM 原始回复（前120字）: {reply[:120].replace(chr(10), ' ')}")

        is_tool, cmd_data = is_tool_call(reply)

        if is_tool:
            action = cmd_data.get("action", "")
            why    = cmd_data.get("why?", "")

            # 检查是否已用过
            if action in used_tools:
                log("WARN", f"AI 尝试重复调用 [{action}]，本轮已禁用，强制注入限制提示并重试")
                extra_context = (
                    f"\n\n【系统强制提示】工具 [{action}] 本轮已执行过，"
                    f"结果已在临时缓存中，请勿再次调用，直接基于缓存结果回复用户。"
                )
                continue

            # 执行工具
            if action == "correction":
                dispatch_tool(cmd_data)
                round_log.append({"action": action, "why": why, "result": None})
                used_tools.add(action)
                extra_context = ""
                log("INFO", "等待 3 秒后重新注入 System Prompt（core.MD 已更新）...")
                time.sleep(3)
            else:
                tool_result = dispatch_tool(cmd_data)
                round_log.append({"action": action, "why": why, "result": tool_result})
                used_tools.add(action)
                extra_context = f"\n\n【工具返回结果 ({action})】\n{tool_result}"

        else:
            # 正常回复
            final_reply = reply
            break

    if not final_reply:
        log("WARN", "工具循环达上限，强制使用最后一次 LLM 回复")
        final_reply = reply

    # 最终缓存摘要
    if round_log:
        print_separator("本轮操作摘要（缓存清空前）")
        print_cache(round_log)
        log("INFO", "临时缓存已清空")

    return final_reply

# ================= 主循环 =================

def start():
    bot_name = CONFIG.get("bot_name", "AI")

    print(f"\n{C.BOLD}{C.MAGENTA}╔══════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}║  {bot_name} 命令行交互模式已启动                  ║{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}╚══════════════════════════════════════════╝{C.RESET}")
    print(f"{C.GREY}  exit/quit → 退出    clear → 清空历史{C.RESET}\n")

    conversation_history = []

    while True:
        try:
            print_separator()
            user_input = input(f"{C.WHITE}{C.BOLD}你 › {C.RESET}").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                log("SYSTEM", "再见！")
                break

            if user_input.lower() == "clear":
                conversation_history.clear()
                log("SYSTEM", "对话历史已清空")
                continue

            log("USER", f"收到输入: {user_input}")
            conversation_history.append({"role": "user", "content": user_input})

            final_reply = process_input(user_input, conversation_history)

            conversation_history.append({"role": "assistant", "content": final_reply})

            print_separator(f"{bot_name} 回复")
            print(f"{C.GREEN}{C.BOLD}{bot_name} › {C.RESET}{C.GREEN}{final_reply}{C.RESET}")
            print_separator()

        except KeyboardInterrupt:
            print(f"\n{C.GREY}（Ctrl+C，输入 exit 可退出）{C.RESET}")
        except Exception as e:
            log("WARN", f"运行异常: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    start()
