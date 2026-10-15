# list_builtin_models.py
import os
from pathlib import Path

def list_sentence_transformers_models():
    """查看sentence-transformers的内置模型"""
    try:
        import sentence_transformers
        print("sentence-transformers版本:", sentence_transformers.__version__)
        
        # 查看模块目录
        st_path = Path(sentence_transformers.__file__).parent
        print(f"sentence-transformers安装路径: {st_path}")
        
        # 查看是否有预定义的模型列表
        models_dir = st_path / "models"
        if models_dir.exists():
            print(f"\n模型目录内容:")
            for item in models_dir.iterdir():
                if item.is_dir():
                    print(f"  📁 {item.name}")
    except Exception as e:
        print(f"错误: {e}")

def list_available_models():
    """尝试加载常见的预训练模型"""
    print("\n尝试加载常见模型...")
    
    # 常见的sentence-transformers模型
    common_models = [
        # 小型模型
        "all-MiniLM-L6-v2",
        "all-MiniLM-L12-v2",
        "paraphrase-MiniLM-L3-v2",
        "paraphrase-MiniLM-L6-v2",
        "all-distilroberta-v1",
        "multi-qa-MiniLM-L6-cos-v1",
        
        # 中型模型
        "all-mpnet-base-v2",
        "multi-qa-mpnet-base-dot-v1",
        "all-roberta-large-v1",
        
        # 多语言模型
        "paraphrase-multilingual-MiniLM-L12-v2",
        "paraphrase-multilingual-mpnet-base-v2",
        
        # BGE系列
        "BAAI/bge-small-en-v1.5",
        "BAAI/bge-base-en-v1.5",
        "BAAI/bge-large-en-v1.5",
        "BAAI/bge-m3",
    ]
    
    available = []
    
    for model_name in common_models:
        try:
            from sentence_transformers import SentenceTransformer
            # 尝试本地加载
            model = SentenceTransformer(model_name, local_files_only=True)
            # 测试编码
            test_emb = model.encode(["test"], normalize_embeddings=True)
            dim = test_emb.shape[1]
            available.append((model_name, dim))
            print(f"✅ {model_name} (维度: {dim})")
        except Exception as e:
            # 只显示成功的模型
            pass
    
    return available

if __name__ == "__main__":
    print("=" * 60)
    print("检查可用的嵌入模型")
    print("=" * 60)
    
    list_sentence_transformers_models()
    
    print("\n" + "=" * 60)
    print("测试模型可用性")
    print("=" * 60)
    
    available_models = list_available_models()
    
    if available_models:
        print(f"\n✅ 找到 {len(available_models)} 个可用模型:")
        for model_name, dim in available_models:
            print(f"  - {model_name} (维度: {dim})")
        
        # 推荐选择
        print(f"\n🌟 推荐使用: {available_models[0][0]}")
        print(f"   维度: {available_models[0][1]}")
    else:
        print("\n❌ 没有找到可用的本地模型")
        print("需要下载模型到本地或使用网络")
