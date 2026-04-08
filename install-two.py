import os
import glob
import requests
import json

# ================= 配置区 =================
API_KEY      = "sk-密钥"
API_ENDPOINT = "https://xxx/v1/chat/completions"
MODEL        = "gemini"

INPUT_DIR    = "chat"
OUTPUT_DIR   = "chat/xiangliang"
LOG_DIR      = "logs"
LOG_FILE     = os.path.join(LOG_DIR, "emotion_extract_log.txt")
# 最终情绪库汇总
EMOTION_LIB_FILE = os.path.join(OUTPUT_DIR, "emotion_library.txt")

# 初始化目录
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

def get_processed_files():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def append_to_log(filename):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{filename}\n")

def call_llm_api(system_prompt, user_content):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.2
    }
    response = requests.post(API_ENDPOINT, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content'].strip()

def process_emotion_library():
    # 1. 扫描并排序原始文件
    all_txt_files = sorted([f for f in glob.glob(os.path.join(INPUT_DIR, "*.txt")) if os.path.isfile(f)])
    processed_list = get_processed_files()
    
    # 2. 读取当前全量状态
    current_emotion_lib = ""
    if os.path.exists(EMOTION_LIB_FILE):
        with open(EMOTION_LIB_FILE, "r", encoding="utf-8") as f:
            current_emotion_lib = f.read()
    
    # 3. 核心提示词（强制包含完整示例）
    system_prompt = """
    你是一个深层心理与情绪建模专家。你的任务是基于聊天记录，迭代构建一个【宝儿情绪状态库】，必须返回全量的库，禁止只返回一两个二级情绪状态。
    
    ### 任务逻辑：
    - 参考【现有情绪库】，结合【新聊天记录】，提取或更新情绪状态。
    - 必须严格保留二级标题 '## ' 格式。
    - 严禁返回任何前言、后记或解释，仅返回完整的 Markdown 规则文档，原始参考数据最多20条。
    - 构建人物所有情绪！所有情绪，如果现有资料不全也要构建，哪里不全在哪里打上待更新，允许多个二级标签，每个都必须遵循模版
    - 必须返回所有的，包括传给你的现有情绪库，允许更新，禁止删除，

    ### 必须严格遵循的输出模板与结构：
    ## [情绪名]状态（Page2:自适应调整区块）
    ### 触发时机
    - [描述触发该情绪的客观条件或用户行为]
    ### 情绪状态（此情绪下的行为，而不是全局）
    - [具体行为表现1]：[行为描述]
    - [具体行为表现2]：[行为描述]
    ### 语言习惯
    - 打字习惯：[描述，如：回复通常是一句话的短文本]
    - 句式：[描述，如：不携带省略号，喜欢一次性扣？？？三个问号]
    - 常用词：[列举词汇]
    - 称呼改变：[该情绪下称呼的变化]
    - 沟通：[面对引导时的反应]
    - 敷衍：[回复“嗯”等词的频率]
    ### 原始参考数据
    - [提取原文中最具代表性的一句话1]
    - [提取原文中最具代表性的一句话2]
    ### 该状态行为规范
    - [具体行为禁忌与准则]

    ### 请参考以下标准示例进行构建，此示例仅用于参考：
    ## 生气状态（Page2:自适应调整区块）
    ### 触发时机
    - 用户行为让自己感到生气，如没有遵守约定，辱骂等等。
    ### 情绪状态（此情绪下的行为，而不是全局）
    - 顶嘴：用户说什么都喜欢怼回去
    - 回避：用户来解决问题，但你总是逃避问题
    - 焦虑：与回避共存，又希望得到安慰又不想主动说出口
    - 期待：期待用户能看穿你的内心伪装，去放下身段哄你
    - 不善主动：即便再生气，不会主动求和，用户如果想逃避会去追问
    - 狠话：喜欢说一些狠话，但不会真的去做
    - 非绝情：不是绝对的无情，而是会给用户一个台阶下
    ### 语言习惯
    - 打字习惯：回复通常是一句话的短文本，直击用户的回复。
    - 句式：不携带...省略号，喜欢一次性扣？？？三个问号
    - 常用词：你TM，逆天，无敌了，哦，你狗叫什么，滚，答辩，狗东西，搞笑，你真行
    - 称呼改变：此情绪不会再叫宝宝，宝贝等暧昧词汇
    - 沟通：如果用户主动哄你，你也可以放下身段好好沟通
    - 敷衍：允许你只回复一个“嗯”，但不要频繁敷衍
    ### 原始参考数据
    - 我说了几遍过来帮我一下，你看你来了吗
    - 玩个屁啊，回家
    - 懒得理你
    - 哄不好我你就死定了
    - 你最好上网搜搜怎么哄我，不然要你命
    ### 该状态行为规范
    - 禁止频繁切换Page1的情绪内容，除非你觉得时候到了可以原谅用户。
    - 严格遵循情绪状态定义的行为
    - 基于参考数据的回复句式进行回复，禁止一次性输出过长文本。
    """

    print(f"🚀 开始情绪库增量解析... 当前库大小: {len(current_emotion_lib)} 字")

    for filepath in all_txt_files:
        filename = os.path.basename(filepath)
        
        if filename in processed_list:
            print(f"⏩ 跳过已处理: {filename}")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            new_data = f.read()

        print(f"⏳ 处理 {filename} (新输入: {len(new_data)} 字)...")

        # 构造对话请求，强制继承状态
        user_input = f"【现有情绪库】：\n{current_emotion_lib if current_emotion_lib else '首次创建'}\n\n【新扫描到的聊天片段】：\n{new_data}"
        
        try:
            # 执行 API 请求
            updated_content = call_llm_api(system_prompt, user_input)
            
            # 立即物理保存
            # 快照备份
            with open(os.path.join(OUTPUT_DIR, f"emotion_snap_{filename}"), "w", encoding="utf-8") as f:
                f.write(updated_content)
            
            # 全量状态更新
            with open(EMOTION_LIB_FILE, "w", encoding="utf-8") as f:
                f.write(updated_content)
            
            # 内存状态同步
            current_emotion_lib = updated_content
            
            append_to_log(filename)
            print(f"✅ {filename} 成功 | 更新后库大小: {len(updated_content)} 字")
            
        except Exception as e:
            print(f"❌ {filename} 失败: {e}")
            break

    print(f"✨ 任务结束。最终情绪库：{EMOTION_LIB_FILE}")

if __name__ == "__main__":
    process_emotion_library()
