import os
import re
import glob
import json
import requests
import datetime

# ===== 配置信息 (来自你的 config.yaml) =====
API_ENDPOINT = "https://api.qnaigc.com/v1/chat/completions"
API_KEY = "sk-144d1c8cc979bec72bb646939f6f49e460a0d06843ff025ac62ba61d9250c43f"
MODEL_NAME = "deepseek/deepseek-v3.2-251201"

# ===== 目录配置 =====
CHAT_DIR = "chat"
PERSONA_DIR = "persona"
LOG_DIR = "log"

EMOTION_FILE = os.path.join(PERSONA_DIR, "emotion.MD")
ACTION_FILE = os.path.join(PERSONA_DIR, "action.MD")

# 确保必要的目录存在
for directory in [CHAT_DIR, PERSONA_DIR, LOG_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

def read_file(filepath):
    """读取文件内容，如果不存在则返回空字符串"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def write_file(filepath, content):
    """写入文件内容"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content.strip())

def call_ai_api(system_prompt, user_prompt):
    """使用 requests 原生请求调用 API，不依赖 openai 库"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.5 # 适中的温度，保证一定逻辑性同时不失细节
    }
    
    try:
        response = requests.post(API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status() # 检查 HTTP 错误
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"API 请求失败: {e}")
        if 'response' in locals() and response is not None:
            print(f"错误详情: {response.text}")
        return None

def main():
    # 匹配类似 01-01.txt, 12-31.txt 格式的日期文件
    # 使用正则匹配确保文件名为两位数-两位数.txt
    all_chat_files = glob.glob(os.path.join(CHAT_DIR, "*.txt"))
    date_pattern = re.compile(r'\d{2}-\d{2}\.txt$')
    chat_files = sorted([f for f in all_chat_files if date_pattern.search(f)])
    
    if not chat_files:
        print(f"在 {CHAT_DIR}/ 目录下未找到符合 00-00.txt 格式的聊天文件。")
        return

    # System Prompt：严格约束输出格式
    system_prompt = """你是一个专业的人物性格、情绪与行为分析专家。
你的任务是阅读给定的聊天记录，提取出新的人物情绪表达和行为模式，并与现有的情绪库和行为库进行融合与全量输出。

【输出要求】
为了配合自动化代码解析，你必须严格按照以下格式全量输出整合后的完整库内容（不要输出任何废话、解释或格式外的文本）：

<EMOTION>
这里填写整合并扩充后的情绪库全量内容（Markdown格式）
</EMOTION>

<ACTION>
这里填写整合并扩充后的行为库全量内容（Markdown格式）
</ACTION>
"""

    for chat_file in chat_files:
        filename = os.path.basename(chat_file)
        print(f"\n--- 正在处理文件: {filename} ---")
        
        # 1. 读取当前已有数据和聊天记录
        current_emotion = read_file(EMOTION_FILE)
        current_action = read_file(ACTION_FILE)
        chat_content = read_file(chat_file)
        
        if not chat_content.strip():
            print(f"{filename} 内容为空，跳过。")
            continue

        # 2. 构造 User Prompt 
        user_prompt = f"""这是现有的情绪库emotion.MD：
{current_emotion if current_emotion else "（暂无内容）"}

这是行为库action.MD：
{current_action if current_action else "（暂无内容）"}

以上是两个库已有内容。这是你需要阅读并补充的聊天记录：
{chat_content}

请根据这些聊天记录，完善并输出最新的情绪库和行为库内容。记住：输出必须被包裹在 <EMOTION> 和 <ACTION> 标签内。
"""

        # 3. 发送 API 请求
        print("正在请求 API 分析并整合数据...")
        raw_response = call_ai_api(system_prompt, user_prompt)
        
        if not raw_response:
            print("未能获取回复，终止当前文件处理。")
            continue

        # 4. 立即保存原始响应到 Log 目录防止丢失
        timestamp = datetime.datetime.now().strftime("%Y%md_%H%M%S")
        log_filename = os.path.join(LOG_DIR, f"log_{filename}_{timestamp}.md")
        write_file(log_filename, raw_response)
        print(f"已保存原始 API 输出至: {log_filename}")

        # 5. 解析并分割回复内容
        emotion_match = re.search(r'<EMOTION>(.*?)</EMOTION>', raw_response, re.DOTALL)
        action_match = re.search(r'<ACTION>(.*?)</ACTION>', raw_response, re.DOTALL)

        if emotion_match and action_match:
            new_emotion = emotion_match.group(1).strip()
            new_action = action_match.group(1).strip()

            # 6. 立即回写并覆盖现有库（实现全量更新与迭进）
            write_file(EMOTION_FILE, new_emotion)
            write_file(ACTION_FILE, new_action)
            print(f"成功更新并覆盖了 {EMOTION_FILE} 和 {ACTION_FILE}")
        else:
            print("警告：API 回复未严格遵守标签格式，无法自动分割写入。请人工检查日志文件。")

    print("\n所有聊天记录处理完毕！")

if __name__ == "__main__":
    main()
