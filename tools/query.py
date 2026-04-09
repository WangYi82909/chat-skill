"""
向量检索脚本 - 基于 .npy 向量文件检索（纯 numpy 版本，无 FAISS）
纯 requests 实现，无 openai 库依赖
"""

import sys
import os
import json
import glob
import numpy as np
import requests

# ================= 配置区 端点别改=================
API_KEY = "sk-密钥"
BASE_URL = "https://xxx.xx/v1"
EMBED_MODEL = "text-embedding-3-large"

# 重排序配置
RERANK_MODEL = "qwen3-rerank"
RERANK_ENDPOINT = "https://xxxx/v1/rerank"

# 本地存储路径
VECTOR_DIR = "./vectors"

# 检索参数
RECALL_TOP_K = 20
RERANK_TOP_N = 3

# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def normalize(vec: list) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr
    return arr / norm

def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """两个已归一化向量的内积即余弦相似度"""
    return np.dot(vec1, vec2)

def _post(endpoint: str, payload: dict) -> dict:
    url = BASE_URL.rstrip("/") + endpoint
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()

def embed_text(text: str) -> list:
    data = _post("/embeddings", {
        "model": EMBED_MODEL,
        "input": text,
    })
    return data["data"][0]["embedding"]

def load_all_vectors():
    """加载所有 .npy 向量文件和元数据"""
    vector_files = sorted(glob.glob(os.path.join(VECTOR_DIR, "*_vectors.npy")))
    
    if not vector_files:
        print(f"错误：{VECTOR_DIR} 目录下未找到向量文件", file=sys.stderr)
        sys.exit(1)
    
    all_vectors = []
    all_metadata = []
    
    for vf in vector_files:
        base_name = os.path.basename(vf).replace("_vectors.npy", "")
        meta_file = os.path.join(VECTOR_DIR, f"{base_name}_meta.npy")
        
        vectors = np.load(vf)
        if os.path.exists(meta_file):
            metadata = np.load(meta_file, allow_pickle=True)
        else:
            metadata = []
        
        for i, vec in enumerate(vectors):
            all_vectors.append(vec)
            if i < len(metadata):
                all_metadata.append({
                    "source_file": f"{base_name}.txt",
                    "chunk_index": metadata[i].get("chunk_index", i+1),
                    "chunk_text": metadata[i].get("chunk_text", ""),
                    "char_count": metadata[i].get("char_count", 0)
                })
                
    return np.array(all_vectors), all_metadata

# ─────────────────────────────────────────
# 核心检索流程
# ─────────────────────────────────────────

def search_and_rerank(query_text: str):
    # ── Step 1：加载向量和元数据 ──
    all_vectors, all_metadata = load_all_vectors()

    # ── Step 2：向量化检索词 ──
    try:
        query_embed = embed_text(query_text)
        query_vec = normalize(query_embed)
    except Exception as e:
        print(f"向量化失败: {e}", file=sys.stderr)
        return

    # ── Step 3：纯 numpy 计算余弦相似度并初筛 ──
    similarities = np.dot(all_vectors, query_vec)
    
    recall_k = min(RECALL_TOP_K, len(similarities))
    top_indices = np.argsort(similarities)[-recall_k:][::-1]
    top_scores = similarities[top_indices]

    candidates = []
    candidate_sources = []
    for idx in top_indices:
        meta = all_metadata[idx]
        candidates.append(meta["chunk_text"])
        candidate_sources.append(meta["source_file"])

    # ── Step 4：重排序 ──
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
        response = requests.post(RERANK_ENDPOINT, headers=headers, json=rerank_payload, timeout=30)
        response.raise_for_status()
        resp_json = response.json()

        rerank_results = resp_json.get("results") or resp_json.get("data")
        if not rerank_results:
            raise ValueError("重排序返回数据为空")

    except Exception:
        # 降级显示向量初筛结果，不打印错误日志以保持输出纯净
        _print_fallback(candidates, candidate_sources, top_scores)
        return

    # ── Step 5：输出精排结果 ──
    for i, item in enumerate(rerank_results):
        idx = item.get("index")
        score = item.get("relevance_score", 0.0)
        text = candidates[idx]
        source = candidate_sources[idx]

        print(f"【排名 {i+1} | 相关度: {score:.4f} | 来源: {source}】\n{text}\n")


def _print_fallback(candidates, sources, scores):
    for i in range(min(RERANK_TOP_N, len(candidates))):
        print(f"【初筛排名 {i+1} | 相似度: {scores[i]:.4f} | 来源: {sources[i]}】\n{candidates[i]}\n")


# ─────────────────────────────────────────
# 入口
# ─────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        user_query = " ".join(sys.argv[1:])
        search_and_rerank(user_query)
