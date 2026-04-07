"""
pipeline.py
阶段 1：chat.txt 按日期切割（辅助模型生成切割脚本并执行）
阶段 2：场景增强（主模型）
阶段 3：向量化 + FAISS / SQLite 入库
纯 requests，无 openai 库依赖
"""

import os
import re
import glob
import sqlite3
import logging
import subprocess
import numpy as np
import requests
from datetime import datetime

try:
    import faiss
except ImportError:
    print("[ERROR] 未检测到 faiss-cpu，请运行：pip install faiss-cpu")
    raise SystemExit(1)

import yaml

# ================= 配置加载 =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config(path="config.yaml"):
    p = os.path.join(BASE_DIR, path)
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

CFG = load_config()

# 主模型
LLM_ENDPOINT     = CFG["llm"]["endpoint"]
LLM_KEY          = CFG["llm"]["key"]
LLM_MODEL        = CFG["llm"]["model"]
LLM_TEMPERATURE  = CFG["llm"]["temperature"]
EMBED_MODEL      = CFG["llm"].get("embed_model", "text-embedding-3-large")
DIMENSION        = CFG["llm"].get("embed_dimension", 3072)

# 辅助模型（切割脚本生成）
AUX_ENDPOINT     = CFG["assistant_llm"]["endpoint"]
AUX_KEY          = CFG["assistant_llm"]["key"]
AUX_MODEL        = CFG["assistant_llm"]["model"]
AUX_SAMPLE_LINES = CFG["assistant_llm"].get("sample_lines", 300)

# 路径
INPUT_CHAT_FILE  = os.path.join(BASE_DIR, "chat.txt")
INPUT_DIR        = os.path.join(BASE_DIR, CFG["paths"].get("chat_dir",  "chat"))
SCENE_DIR        = os.path.join(BASE_DIR, CFG["paths"].get("scene_dir", "chat/xiangliang"))
DB_DIR           = os.path.join(BASE_DIR, CFG["paths"].get("db_dir",    "vector_db"))
LOG_DIR          = os.path.join(BASE_DIR, CFG["paths"]["log_dir"])

FAISS_INDEX_PATH = os.path.join(DB_DIR, "chat.index")
SQLITE_PATH      = os.path.join(DB_DIR, "chat_meta.db")
ID_COUNTER_PATH  = os.path.join(DB_DIR, "id_counter.txt")
DONE_LOG         = os.path.join(LOG_DIR, "pipeline_done.txt")
SPLIT_SCRIPT     = os.path.join(BASE_DIR, "auto_splitter.py")

for _d in [INPUT_DIR, SCENE_DIR, DB_DIR, LOG_DIR]:
    os.makedirs(_d, exist_ok=True)

# ================= 双写日志 =================

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
    lg.info(f"日志文件: {log_file}")
    return lg

log = setup_logger()

# ================= 增量记录 =================

def get_done_files() -> set:
    if not os.path.exists(DONE_LOG):
        return set()
    with open(DONE_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def mark_done(filename: str):
    with open(DONE_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

# ================= 纯 requests HTTP =================

def _post(base_url: str, api_key: str, endpoint: str, payload: dict) -> dict:
    url  = base_url.rstrip("/") + endpoint
    hdrs = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = requests.post(url, headers=hdrs, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()

def chat_complete(system: str, user: str,
                  base_url=None, api_key=None,
                  model=None, temperature=None) -> str:
    data = _post(
        base_url    or LLM_ENDPOINT,
        api_key     or LLM_KEY,
        "/chat/completions",
        {
            "model":       model       or LLM_MODEL,
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
    data = _post(LLM_ENDPOINT, LLM_KEY, "/embeddings", {
        "model": EMBED_MODEL,
        "input": text,
    })
    return data["data"][0]["embedding"]

# ================= 向量归一化 =================

def normalize(vec: list) -> np.ndarray:
    arr  = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return arr / norm if norm > 0 else arr

# ================= FAISS =================

def init_faiss() -> faiss.Index:
    if os.path.exists(FAISS_INDEX_PATH):
        log.info(f"加载已有 FAISS 索引: {FAISS_INDEX_PATH}")
        return faiss.read_index(FAISS_INDEX_PATH)
    log.info(f"新建 FAISS 索引 (dim={DIMENSION}, 余弦相似度)")
    return faiss.IndexFlatIP(DIMENSION)

def save_faiss(index: faiss.Index):
    faiss.write_index(index, FAISS_INDEX_PATH)
    log.info(f"FAISS 已保存: {FAISS_INDEX_PATH} (共 {index.ntotal} 条)")

# ================= SQLite =================

def init_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            faiss_id    INTEGER PRIMARY KEY,
            text        TEXT NOT NULL,
            source_file TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

# ================= 自增 ID =================

def get_current_id() -> int:
    if not os.path.exists(ID_COUNTER_PATH):
        return 0
    with open(ID_COUNTER_PATH, "r") as f:
        return int(f.read().strip())

def save_current_id(val: int):
    with open(ID_COUNTER_PATH, "w") as f:
        f.write(str(val))

# ================= 用户确认 =================

def ask_user(question: str) -> bool:
    while True:
        ans = input(f"\n{question} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("请输入 y 或 n")

# ================= 阶段 1：切割 =================

def stage_split() -> bool:
    log.info("=" * 55)
    log.info("阶段 1：检查 chat.txt 切割状态")

    existing = glob.glob(os.path.join(INPUT_DIR, "??-??.txt"))
    if existing:
        log.info(f"chat/ 下已存在 {len(existing)} 个日期文件，跳过切割")
        log.info("示例: " + ", ".join(os.path.basename(f) for f in existing[:5]))
        return True

    if not os.path.exists(INPUT_CHAT_FILE):
        log.warning(f"未找到 {INPUT_CHAT_FILE}，跳过切割阶段")
        return False

    log.info(f"chat/ 目录为空，准备切割 chat.txt")
    log.info(f"辅助模型: {AUX_MODEL} @ {AUX_ENDPOINT}")

    with open(INPUT_CHAT_FILE, "r", encoding="utf-8") as f:
        sample_lines = [f.readline() for _ in range(AUX_SAMPLE_LINES)]
    sample_text = "".join(sample_lines)

    prompt = f"""
你是一个 Python 专家。请根据以下 chat.txt 的前 {AUX_SAMPLE_LINES} 行内容，编写一个自动化切割脚本。

【输入文件格式说明】：
{sample_text}

【需求详情】：
1. 读取整个 'chat.txt'。
2. 将对话按日期切割，保存到名为 'chat' 的目录中。
3. 每个文件以 '月-日.txt' 命名（如 07-29.txt）。
4. 文件内第一行必须是 '--月-日'。
5. 过滤掉所有包含"已添加了"、"现在可以开始聊天了"等系统提示。
6. 对话内容必须严格遵守以下格式：
   发送方名称：消息内容
7. 剔除无用的语句，如[表情包]等等。

【输出要求】：
只输出 Python 代码，严禁包含 Markdown 代码块标记（如 ```python），直接输出纯代码，确保保存后可以直接运行。
"""

    log.info("调用辅助模型生成切割脚本...")
    try:
        generated_code = chat_complete(
            system="你是一个只输出纯代码的脚本生成器。",
            user=prompt,
            base_url=AUX_ENDPOINT,
            api_key=AUX_KEY,
            model=AUX_MODEL,
            temperature=0.1,
        )
    except Exception as e:
        log.error(f"辅助模型调用失败: {e}")
        return False

    # 清洗 markdown 标记
    generated_code = re.sub(
        r'^```python\s*\n|^```\s*\n|^```python|^```|```$',
        '', generated_code, flags=re.MULTILINE
    ).strip()

    with open(SPLIT_SCRIPT, "w", encoding="utf-8") as f:
        f.write(generated_code)
    log.info(f"切割脚本已保存: {SPLIT_SCRIPT}")

    log.info("执行切割脚本...")
    try:
        try:
            subprocess.run(["python", SPLIT_SCRIPT], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            subprocess.run(["python3", SPLIT_SCRIPT], check=True)
    except subprocess.CalledProcessError as e:
        log.error(f"切割脚本执行失败，退出码: {e.returncode}")
        return False
    except Exception as e:
        log.error(f"切割脚本执行异常: {e}")
        return False

    result = glob.glob(os.path.join(INPUT_DIR, "??-??.txt"))
    log.info(f"切割完成，生成 {len(result)} 个日期文件")
    return len(result) > 0

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

    all_files = sorted(glob.glob(os.path.join(INPUT_DIR, "??-??.txt")))
    if not all_files:
        log.warning("chat/ 目录下未找到日期文件，跳过场景增强")
        return

    done_set = get_done_files()
    pending  = [f for f in all_files
                if os.path.basename(f) not in done_set
                and not os.path.exists(os.path.join(SCENE_DIR, os.path.basename(f)))]

    log.info(f"共 {len(all_files)} 个文件，待增强 {len(pending)} 个")

    for filepath in pending:
        filename   = os.path.basename(filepath)
        scene_path = os.path.join(SCENE_DIR, filename)

        with open(filepath, "r", encoding="utf-8") as f:
            raw_text = f.read()
        log.info(f"增强中: {filename}  原文 {len(raw_text)} 字")

        try:
            enhanced = chat_complete(
                ENHANCE_SYSTEM,
                f"请处理以下原始聊天记录，保留原文并增强语境：\n\n{raw_text}"
            )
        except Exception as e:
            log.error(f"增强失败，跳过 {filename}: {e}")
            continue

        with open(scene_path, "w", encoding="utf-8") as f:
            f.write(enhanced)
        log.info(f"增强完成: {scene_path}  ({len(enhanced)} 字)")

    log.info("场景增强阶段完成")

# ================= 阶段 3：向量入库 =================

def stage_embed():
    log.info("=" * 55)
    log.info("阶段 3：向量化入库 (FAISS + SQLite)")

    index    = init_faiss()
    conn     = init_sqlite()
    done_set = get_done_files()
    cur_id   = get_current_id()

    files = sorted(glob.glob(os.path.join(SCENE_DIR, "*.txt")))
    if not files:
        log.warning("chat/xiangliang/ 目录下没有文件，跳过入库")
        conn.close()
        return

    pending = [f for f in files if os.path.basename(f) not in done_set]
    log.info(f"共 {len(files)} 个增强文件，待入库 {len(pending)} 个")

    for filepath in pending:
        filename = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        chunks    = [c.strip() for c in content.split("---") if c.strip()]
        ok_count  = 0
        err_count = 0
        log.info(f"处理文件: {filename}  ({len(chunks)} 个片段)")

        for i, chunk in enumerate(chunks, start=1):
            try:
                norm_vec = normalize(embed_text(chunk))
            except Exception as e:
                log.warning(f"片段 {i}/{len(chunks)} 向量化失败: {e}")
                err_count += 1
                continue

            index.add(norm_vec.reshape(1, -1))
            conn.execute(
                "INSERT INTO chunks (faiss_id, text, source_file) VALUES (?, ?, ?)",
                (cur_id, chunk, filename)
            )
            conn.commit()
            save_faiss(index)
            cur_id += 1
            save_current_id(cur_id)
            ok_count += 1
            log.info(f"片段 {i}/{len(chunks)} 入库  faiss_id={cur_id - 1}  字数={len(chunk)}")

        mark_done(filename)
        log.info(
            f"文件完成: {filename}  成功={ok_count}  失败={err_count}  "
            f"FAISS 累计={index.ntotal}"
        )
        log.info("-" * 55)

    conn.close()
    log.info("全部文件处理完毕")
    log.info(f"FAISS 索引    : {FAISS_INDEX_PATH}")
    log.info(f"SQLite 元数据 : {SQLITE_PATH}")
    log.info(f"当前总向量数  : {index.ntotal}")

# ================= 主入口 =================

def main():
    log.info("pipeline 启动")

    # 阶段 1：切割
    split_ok = stage_split()
    if not split_ok:
        log.warning("chat/ 下无有效日期文件，流程终止")
        return

    date_files = glob.glob(os.path.join(INPUT_DIR, "??-??.txt"))
    print(f"\n[INFO] chat/ 目录下检测到 {len(date_files)} 个日期文件")

    # 询问是否进行向量处理前准备
    if not ask_user("是否开始向量处理前准备（场景增强）？"):
        log.info("用户跳过场景增强，流程结束")
        return

    # 阶段 2：场景增强
    stage_enhance()

    # 询问是否进行向量入库
    if not ask_user("场景增强已完成，是否开始向量化并插入数据库？"):
        log.info("用户跳过向量入库，流程结束")
        return

    # 阶段 3：向量入库
    stage_embed()
    log.info("pipeline 全流程完成")


if __name__ == "__main__":
    main()
