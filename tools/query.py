"""
向量检索脚本 - 使用 FAISS + SQLite + Rerank（替代 Milvus Lite）
依赖安装：pip install faiss-cpu openai numpy requests
"""

import sys
import os
import json
import sqlite3
import numpy as np
import requests
from openai import OpenAI

try:
    import faiss
except ImportError:
    print("❌ 错误：未检测到 faiss-cpu。请运行: pip install faiss-cpu")
    sys.exit(1)

# ================= 配置区 3072维度信息需要自行更改=================
API_KEY = "sk-"
BASE_URL = "https://xxxxxxx/v1"
EMBED_MODEL = "text-embedding-3-large"
DIMENSION = 3072

# 重排序配置
RERANK_MODEL = "qwen3-rerank"
RERANK_ENDPOINT = "https://yunwu.ai/v1/rerank"

# 本地存储路径（需与入库脚本一致）
DB_DIR = "./vector_db"
FAISS_INDEX_PATH = os.path.join(DB_DIR, "chat.index")
SQLITE_PATH = os.path.join(DB_DIR, "chat_meta.db")

# 检索参数
RECALL_TOP_K = 20    # 向量初筛候选数
RERANK_TOP_N = 3     # 重排后返回数

client_ai = OpenAI(api_key=API_KEY, base_url=BASE_URL)


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def normalize(vec: list) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr
    return arr / norm

def load_faiss_index():
    if not os.path.exists(FAISS_INDEX_PATH):
        print(f"❌ 找不到 FAISS 索引文件: {FAISS_INDEX_PATH}")
        print("请先运行 two_faiss.py 完成向量入库。")
        sys.exit(1)
    index = faiss.read_index(FAISS_INDEX_PATH)
    print(f"📂 已加载 FAISS 索引，共 {index.ntotal} 条向量")
    return index

def open_sqlite():
    if not os.path.exists(SQLITE_PATH):
        print(f"❌ 找不到 SQLite 数据库: {SQLITE_PATH}")
        print("请先运行 two_faiss.py 完成向量入库。")
        sys.exit(1)
    return sqlite3.connect(SQLITE_PATH)

def fetch_metadata_by_ids(conn, ids):
    """批量查询元数据，返回 {faiss_id: (text, source_file)} 字典"""
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT faiss_id, text, source_file FROM chunks WHERE faiss_id IN ({placeholders})",
        [int(i) for i in ids]
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


# ─────────────────────────────────────────
# 核心检索流程
# ─────────────────────────────────────────

def search_and_rerank(query_text: str):
    print(f"\n🔍 检索词: 「{query_text}」 | 字数: {len(query_text)}")
    print("=" * 55)

    # ── Step 1：加载索引和数据库 ──
    index = load_faiss_index()
    conn = open_sqlite()

    # ── Step 2：向量化检索词 ──
    print("⏳ 正在向量化检索词...")
    try:
        embed_resp = client_ai.embeddings.create(input=query_text, model=EMBED_MODEL)
        query_vec = normalize(embed_resp.data[0].embedding)
        query_matrix = query_vec.reshape(1, -1)  # shape: (1, DIMENSION)
    except Exception as e:
        print(f"❌ 向量化失败: {e}")
        conn.close()
        return

    # ── Step 3：FAISS 向量初筛 ──
    print(f"⏳ FAISS 向量空间初筛 Top-{RECALL_TOP_K}...")
    recall_k = min(RECALL_TOP_K, index.ntotal)
    scores, indices = index.search(query_matrix, recall_k)

    # indices[0] 是检索到的 faiss_id 数组
    hit_ids = [int(idx) for idx in indices[0] if idx >= 0]
    hit_scores = scores[0][:len(hit_ids)]

    if not hit_ids:
        print("❌ 数据库中未找到相关片段。")
        conn.close()
        return

    # 查询元数据
    meta_map = fetch_metadata_by_ids(conn, hit_ids)
    conn.close()

    candidates = []
    candidate_sources = []
    for fid in hit_ids:
        if fid in meta_map:
            text, source = meta_map[fid]
            candidates.append(text)
            candidate_sources.append(source)

    print(f"✅ 初筛完成，锁定 {len(candidates)} 个候选片段")

    # ── Step 4：重排序 ──
    print(f"🚀 调用 {RERANK_MODEL} 进行深度重排序...")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    rerank_payload = {
        "model": RERANK_MODEL,
        "query": query_text,
        "documents": candidates,
        "top_n": RERANK_TOP_N
    }

    try:
        total_input_chars = len(query_text) + sum(len(c) for c in candidates)
        response = requests.post(RERANK_ENDPOINT, headers=headers, json=rerank_payload, timeout=30)
        response.raise_for_status()
        resp_json = response.json()

        rerank_results = resp_json.get("results") or resp_json.get("data")
        if not rerank_results:
            raise ValueError("重排序返回数据为空")

        output_chars = len(json.dumps(resp_json, ensure_ascii=False))
        print(f"✅ 重排完成 | 发送: {total_input_chars} 字 | 接收: {output_chars} 字")

    except Exception as e:
        print(f"❌ 重排序失败: {e}")
        print("⚠️  降级显示向量初筛结果（按余弦相似度排序）：\n")
        _print_fallback(candidates, candidate_sources, hit_scores)
        return

    # ── Step 5：输出精排结果 ──
    print("\n" + "🏆" + "=" * 20 + " 深度重排结果 " + "=" * 20 + "🏆")
    for i, item in enumerate(rerank_results):
        idx = item.get("index")
        score = item.get("relevance_score", 0.0)
        text = candidates[idx]
        source = candidate_sources[idx]

        print(f"\n【排名 {i+1} | 相关度: {score:.4f} | 来源: {source} | 字数: {len(text)}】")
        print(text)
        print("-" * 55)


def _print_fallback(candidates, sources, scores):
    """重排失败时的降级显示"""
    print("=" * 55)
    for i in range(min(RERANK_TOP_N, len(candidates))):
        print(f"【初筛 {i+1} | 余弦相似度: {scores[i]:.4f} | 来源: {sources[i]} | 字数: {len(candidates[i])}】")
        print(candidates[i])
        print("-" * 55)


# ─────────────────────────────────────────
# 入口
# ─────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        user_query = " ".join(sys.argv[1:])
        search_and_rerank(user_query)
    else:
        print("💡 使用说明：")
        print("  python query_faiss.py <检索词>")
        print("  示例：python query_faiss.py 宝儿因为没睡觉生气了")
