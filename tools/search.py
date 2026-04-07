import os
import sys

# ================= 配置区 =================
# 扫描 persona 文件夹下的所有 .MD 文件
PERSONA_DIR = "../persona"

def get_abs_path(rel_path):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, rel_path))

def parse_files():
    """
    解析数据结构：
    {
        "标题名": {
            "file": "文件名",
            "full_text": "完整块内容",
            "trigger": "提取出的###触发时机内容"
        }
    }
    """
    abs_persona_dir = get_abs_path(PERSONA_DIR)
    data_map = {}
    
    if not os.path.exists(abs_persona_dir):
        return data_map

    # 遍历 persona 目录下所有 .MD 文件
    for filename in os.listdir(abs_persona_dir):
        if not filename.lower().endswith(".md"):
            continue
            
        file_path = os.path.join(abs_persona_dir, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 按 ## 切分块
        # 补齐第一个 ## 之前的内容（如果有）
        if not content.startswith("## "):
            content = "## " + content.split("## ", 1)[-1] if "## " in content else ""
            
        sections = content.split("\n## ")
        
        for section in sections:
            if not section.strip(): continue
            
            # 还原二级标题格式
            full_block = "## " + section if not section.startswith("## ") else section
            lines = full_block.split("\n")
            
            # 提取二级标题名
            title = lines[0].replace("##", "").strip()
            
            # 提取 ### 触发时机块
            trigger_content = "未找到触发时机"
            if "### 触发时机" in full_block:
                try:
                    # 截取从 ### 触发时机 到下一个 ### 之前的内容
                    parts = full_block.split("### 触发时机")
                    trigger_part = parts[1].split("###")[0].strip()
                    trigger_content = trigger_part
                except:
                    pass
            
            data_map[title] = {
                "file": filename,
                "full_text": full_block,
                "trigger": trigger_content
            }
            
    return data_map

def main():
    data_map = parse_files()
    
    if not data_map:
        print(f"❌ 错误：在 {PERSONA_DIR} 未找到有效的 MD 文件或二级标题。")
        return

    # 模式判断
    args = sys.argv[1:]

    # --- 模式 1：直接运行 (列出所有二级标题) ---
    if len(args) == 0:
        current_file = ""
        for title, info in data_map.items():
            if info['file'] != current_file:
                current_file = info['file']
                print(f"{current_file}")
            print(f"{title}")
        return

    # --- 模式 2：参数为 "all" (列出标题 + 触发时机) ---
    if args[0].lower() == "all":
        for title, info in data_map.items():
            print(f"【{info['file']}】## {title}")
            print(f"### 触发时机\n{info['trigger']}\n" + "-"*30)
        return

    # --- 模式 3：传入关键词 (返回完整内容) ---
    query = " ".join(args).strip()
    found = False
    for title, info in data_map.items():
        if query in title:
            print(info['full_text'])
            found = True
            break
            
    if not found:
        print(f"未找到匹配项: {query}")

if __name__ == "__main__":
    main()
