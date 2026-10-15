from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")
# 运行完模型就下载到本机缓存目录了（一般在 ~/.cache/huggingface）
