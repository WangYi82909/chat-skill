import os
import re
import glob
import logging
import subprocess
import numpy as np
import requests
import yaml
from datetime import datetime

# ================= 配置加载 =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config(path="config.yaml"):
    p = os.path.join(BASE_DIR, path)
    if not os.path.exists(p):
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
        if cfg is None:
            raise ValueError(f"配置文件为空或格式错误: {p}")
        return cfg

CFG = load_config()

# 主模型配置
LLM_ENDPOINT     = CFG.get("llm_endpoint") or CFG.get("llm", {}).get("endpoint")
LLM_KEY          = CFG.get("llm_key")      or CFG.get("llm", {}).get("key")
LLM_MODEL        = CFG.get("llm_model", "deepseek/deepseek-v3")
LLM_TEMPERATURE  = CFG.get("llm_temperature", 0.8)
EMBED_MODEL      = CFG.get("embed_model", "text-embedding-3-large")

# 辅助模型配置
AUX_ENDPOINT     = CFG.get("assistant_endpoint", LLM_ENDPOINT)
AUX_KEY          = CFG.get("assistant_key", LLM_KEY)
AUX_MODEL        = CFG.get("assistant_model", LLM_MODEL)
AUX_SAMPLE_LINES = 300

# 路径配置
INPUT_CHAT_FILE  = os.path.join(BASE_DIR, "chat.txt")
INPUT_DIR        = os.path.join(BASE_DIR, CFG.get("chat_dir", "chat"))
SCENE_DIR        = os.path.join(BASE_DIR, CFG.get("scene_dir", "chat/xiangliang"))
VECTOR_DIR       = os.path.join(BASE_DIR, CFG.get("vector_dir", "vectors"))
LOG_DIR          = os.path.join(BASE_DIR, CFG.get("log_dir", "logs"))

# 状态记录
SCENE_DONE_LOG   = os.path.join(LOG_DIR, "scene_enhance_done.txt")
VECTOR_DONE_LOG  = os.path.join(LOG_DIR, "vector_extract_done.txt")
SPLIT_SCRIPT     = os.path.join(BASE_DIR, "auto_splitter.py")

for _d in [INPUT_DIR, SCENE_DIR, VECTOR_DIR, LOG_DIR]:
    os.makedirs(_d, exist_ok=True)

# ================= 日志设置 =================

def setup_logger():
    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"pipeline_{run_ts}.log")
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    fh  = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    ch  = logging.StreamHandler()
    ch.setFormatter(fmt)
    lg  = logging.getLogger("pipeline")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(fh)
    lg.addHandler(ch)
    return lg

log = setup_logger()

# ================= 增量记录逻辑 =================

def get_done_list(log_path) -> set:
    if not os.path.exists(log_path):
        return set()
    with open(log_path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def mark_done(log_path, filename: str):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

# ================= 智能 URL 补全与请求 =================

def safe_url_join(base, path):
    """智能处理 URL 拼接，确保 path 前缀正确"""
    base = base.strip().rstrip('/')
    if path.startswith('/'):
        return base + path
    return base + '/' + path

def _post(base_url: str, api_key: str, endpoint_type: str, payload: dict) -> dict:
    """
    endpoint_type: 'chat' 或 'embed'
    """
    # 智能补全逻辑
    full_url = base_url.strip()
    
    if endpoint_type == 'chat':
        # 如果 URL 里没写 /chat/completions，就补上
        if "/chat/completions" not in full_url:
            full_url = safe_url_join(full_url, "/chat/completions")
    elif endpoint_type == 'embed':
        if "/embeddings" not in full_url:
            full_url = safe_url_join(full_url, "/embeddings")

    hdrs = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    
    resp = requests.post(full_url, headers=hdrs, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()

def chat_complete(system: str, user: str,
                  base_url=None, api_key=None,
                  model=None, temperature=None) -> str:
    data = _post(
        base_url or LLM_ENDPOINT,
        api_key  or LLM_KEY,
        'chat',
        {
            "model":       model or LLM_MODEL,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
        }
    )
    return data["choices"][0]["message"]["content"].strip()

def embed_text(text: str) -> list:
    data = _post(LLM_ENDPOINT, LLM_KEY, 'embed', {
        "model": EMBED_MODEL,
        "input": text,
    })
    return data["data"][0]["embedding"]

def normalize(vec: list) -> np.ndarray:
    arr  = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return arr / norm if norm > 0 else arr

def ask_user(question: str) -> bool:
    while True:
        ans = input(f"\n{question} [y/n]: ").strip().lower()
        if ans in ("y", "yes"): return True
        if ans in ("n", "no"): return False

# ================= 阶段 1：切割脚本自动化 =================

def stage_split() -> bool:
    log.info("=" * 55)
    log.info("阶段 1：检查 chat.txt 切割状态")

    existing = glob.glob(os.path.join(INPUT_DIR, "*.txt"))
    if existing:
        log.info(f"目录已存在 {len(existing)} 个文件，跳过切割")
        return True

    if not os.path.exists(INPUT_CHAT_FILE):
        log.warning(f"未找到 {INPUT_CHAT_FILE}，无法切割")
        return False

    with open(INPUT_CHAT_FILE, "r", encoding="utf-8") as f:
        sample = "".join([f.readline() for _ in range(AUX_SAMPLE_LINES)])

    prompt = f"""
你是一个 Python 专家。请根据以下 chat.txt 的前 {AUX_SAMPLE_LINES} 行内容，编写一个自动化切割脚本。

【输入文件格式】：
{sample}

【需求】：
1. 读取 'chat.txt'。
2. 按日期切割，保存到 'chat' 目录。
3. 文件以 '月-日.txt' 命名。
4. 文件第一行必须是 '--月-日'。
5. 过滤包含"已添加了"、"现在可以开始聊天了"等系统提示。
6. 格式：发送方名称：消息内容。
7. 剔除[表情包]等无用语句。

【输出要求】：
只输出纯 Python 代码，严禁 Markdown 标记。
"""
    log.info("调用辅助模型生成脚本...")
    code = chat_complete(
        "你是一个只输出纯代码的生成器。", prompt,
        base_url=AUX_ENDPOINT, api_key=AUX_KEY, model=AUX_MODEL, temperature=0.1
    )
    code = re.sub(r'^```python\s*\n|^```\s*\n|^```python|^```|```$', '', code, flags=re.MULTILINE).strip()

    with open(SPLIT_SCRIPT, "w", encoding="utf-8") as f: f.write(code)
    
    try:
        subprocess.run(["python", SPLIT_SCRIPT], check=True)
    except:
        subprocess.run(["python3", SPLIT_SCRIPT], check=True)
    
    return len(glob.glob(os.path.join(INPUT_DIR, "*.txt"))) > 0

# ================= 阶段 2：场景增强 =================

ENHANCE_SYSTEM = """
你是一个专业的数据增强助手。你的任务是处理原始聊天记录，将其切分为逻辑完整的对话块。

严格执行要求：
1. 保留原始对话：必须完整保留每一轮对话的原始内容和语气，严禁进行总结或改写对话文字。
2. 补充语境标签：在每个对话块的最上方，添加一行 [场景与语境背景：...]，深度还原该片段发生的背景、情绪基调或隐晦意图。
3. 逻辑分块：每个对话块包含 3-8 轮对话，块与块之间使用 "---" 分隔。
4. 清洗噪声：删除 [表情包]、系统提示音（如"撤回一条消息"）。
5. 输出纯净度：仅返回处理后的对话块及标签，禁止包含任何废话前缀。
"""

def stage_enhance():
    log.info("=" * 55)
    log.info("阶段 2：场景增强")
    all_files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.txt")))
    done_set = get_done_list(SCENE_DONE_LOG)
    pending = [f for f in all_files if os.path.basename(f) not in done_set]

    for filepath in pending:
        fname = os.path.basename(filepath)
        log.info(f"处理中: {fname}")
        with open(filepath, "r", encoding="utf-8") as f: raw = f.read()
        try:
            res = chat_complete(ENHANCE_SYSTEM, f"请处理原始记录并增强语境：\n\n{raw}", 
                                base_url=AUX_ENDPOINT, api_key=AUX_KEY, model=AUX_MODEL, temperature=0.3)
            with open(os.path.join(SCENE_DIR, fname), "w", encoding="utf-8") as f: f.write(res)
            mark_done(SCENE_DONE_LOG, fname)
        except Exception as e:
            log.error(f"失败 {fname}: {e}")

# ================= 阶段 3：向量提取 (NPY) =================

def stage_extract():
    log.info("=" * 55)
    log.info("阶段 3：向量提取与保存 (.npy)")
    files = sorted(glob.glob(os.path.join(SCENE_DIR, "*.txt")))
    done_set = get_done_list(VECTOR_DONE_LOG)
    pending = [f for f in files if os.path.basename(f) not in done_set]

    for filepath in pending:
        fname = os.path.basename(filepath)
        base = os.path.splitext(fname)[0]
        with open(filepath, "r", encoding="utf-8") as f: content = f.read()
        chunks = [c.strip() for c in content.split("---") if c.strip()]
        
        vectors, metas = [], []
        for i, chunk in enumerate(chunks, start=1):
            try:
                vec = normalize(embed_text(chunk))
                vectors.append(vec)
                metas.append({"index": i, "text": chunk, "file": fname})
                log.info(f"  {fname} [{i}/{len(chunks)}] 成功")
            except Exception as e:
                log.warning(f"  跳过片段: {e}")

        if vectors:
            np.save(os.path.join(VECTOR_DIR, f"{base}_vectors.npy"), np.vstack(vectors))
            np.save(os.path.join(VECTOR_DIR, f"{base}_meta.npy"), np.array(metas, dtype=object))
            mark_done(VECTOR_DONE_LOG, fname)
            log.info(f"保存完毕: {fname}")

# ================= 主入口 =================

def main():
    log.info("Pipeline 启动")
    if not stage_split(): return
    
    if ask_user("是否进行场景增强？"):
        stage_enhance()
        
    if ask_user("是否提取向量并保存？"):
        stage_extract()

    log.info("全流程结束")

if __name__ == "__main__":
    main()
