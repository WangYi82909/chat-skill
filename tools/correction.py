import sys
import os
import re

# ================= 配置区 =================
ACTION_FILE  = "../persona/action.MD"
EMOTION_FILE = "../persona/emotion.MD"
CORE_FILE    = "../persona/core.MD"

def get_abs_path(rel_path):
    """获取相对于当前脚本目录的绝对路径"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, rel_path))

def fetch_block(file_path, tag_keyword):
    """使用正则从源文件中精准抓取包含关键词的二级标题完整块"""
    abs_path = get_abs_path(file_path)
    filename = os.path.basename(file_path)
    
    if not os.path.exists(abs_path):
        return f"> ❌ 错误：找不到文件 {filename}"
    
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 终极正则匹配逻辑：
    # ^##[ \t]* 匹配行首的 ##（兼容后面有无空格）
    # [^\n]+ 匹配标题内容
    # .*? 匹配正文
    # (?=\n##[ \t]|\Z) 停止于下一个 ## 或文件末尾
    pattern = re.compile(r"(^##[ \t]*[^\n]+.*?)(?=\n##[ \t]|\Z)", re.MULTILINE | re.DOTALL)
    blocks = pattern.findall(content)
    
    for block in blocks:
        # 取块的第一行作为标题行
        title_line = block.strip().split("\n")[0]
        if tag_keyword in title_line:
            return block.strip()
            
    # 如果没找到，返回显式可见的 Markdown 错误提示，而不是隐形的 HTML 注释
    return f"> ❌ 未在 {filename} 中找到包含 '{tag_keyword}' 的标签块"

def update_core_md(new_content):
    """将新规则精准补全到 # 行为规则 下方，不破坏后续工具调用"""
    abs_core_path = get_abs_path(CORE_FILE)
    if not os.path.exists(abs_core_path):
        print(f"❌ 错误：核心人格文件 {CORE_FILE} 不存在")
        return False

    with open(abs_core_path, "r", encoding="utf-8") as f:
        core_text = f.read()

    # 强化版正则匹配逻辑：
    pattern = re.compile(r"(#\s*行为规则[^\n]*\n)(.*?)(?=^##?\s*工具调用)", re.MULTILINE | re.DOTALL)
    
    # 构造替换文本，保留标题行并插入提取到的内容，保持两端有空行
    replacement = f"\\1\n{new_content}\n\n"
    
    # 执行替换
    if pattern.search(core_text):
        new_core_text = pattern.sub(replacement, core_text)
        with open(abs_core_path, "w", encoding="utf-8") as f:
            f.write(new_core_text)
        return True
    else:
        print("❌ 匹配失败诊断信息：请检查 core.MD 中的标记。")
        return False

def main():
    if len(sys.argv) < 3:
        print("用法: python3 correction.py <行为关键词> <情绪关键词>")
        sys.exit(1)

    behavior_key = sys.argv[1]
    emotion_key = sys.argv[2]

    print(f"正在从库中提取：行为[{behavior_key}] + 情绪[{emotion_key}]...")

    # 1. 从各自的文件提取对应的二级块
    behavior_part = fetch_block(ACTION_FILE, behavior_key)
    emotion_part = fetch_block(EMOTION_FILE, emotion_key)

    # 2. 拼接新规则
    combined_rules = f"{behavior_part}\n\n{emotion_part}"

    # 3. 物理写入 core.MD 的指定区域
    if update_core_md(combined_rules):
        print(f"补全好了 {CORE_FILE} ")
    else:
        print("❌ 补全操作未成功。")

if __name__ == "__main__":
    main()
