"""config.py —— review 服务（8100）环境变量驱动配置（pydantic-settings）。

全部键可用环境变量覆盖（前缀 REVIEW_）。LLM 沿用 docgen 的裸键模式：
优先 REVIEW_LLM_*，回退 DOCGEN_LLM_*（与 paper_writer_service 共享同一套
DeepSeek 凭据，由 services/llm.py 直接读 os.environ）。零硬编码宿主路径。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="REVIEW_",
    )

    app_name: str = "OMaaX-education"
    version: str = "0.1.0"

    # ---- 自审稿（presubmit）----
    presubmit_read_timeout: float = 360.0  # LLM 长调用读超时（秒）
    max_text_chars: int = 150000            # 送 LLM 的正文截断上限（REVIEW_MAX_TEXT_CHARS 覆盖；
                                            # token 超 48K 自动降 100K，LLM 400/413 再降 80K 重试）
    max_pdf_bytes: int = 50 * 1024 * 1024   # 上传 PDF 上限 50MB（对齐 extraction FR-EXTRACT-004）

    # ---- 期刊情报与投稿推荐（recommend）----
    db_path: str = "review.db"              # 独立 SQLite（相对 review_service/；REVIEW_DB_PATH 覆盖）
    journals_seed_path: str = "data/journals/journals.json"  # 种子（相对 review_service/）
    journal_directory_seed_path: str = "data/journals/source_v4.xlsx"  # 目录源 xlsx（相对 review_service/）
    seed_on_startup: bool = True            # 启动自动 seed journal_directory（空表才导；false 关闭）
    recommend_read_timeout: float = 360.0   # LLM 重排读超时（秒）
    recommend_query_chars: int = 3000       # 送 vstore 的检索 query 截断（防嵌入模型超长）
    recommend_scope_top_k: int = 10         # search-chunks top_k（vstore 上限 20）

    # ---- 科研汇报 PPT（P5：map-reduce 大纲 + python-pptx 初稿）----
    ppt_jobs_dir: str = "data/ppt_jobs"      # 产物（draft.pptx + meta，相对 review_service/）
    ppt_styles_dir: str = "data/ppt_styles"  # 风格 tokens JSON（源码资产，随 git 走）

    @property
    def _service_root(self) -> "Path":
        # review_service/app/config.py → review_service/（相对本文件定位，不依赖 cwd）
        return Path(__file__).resolve().parent.parent

    @property
    def db_path_abs(self) -> "Path":
        # 绝对 db_path 会被 Path 除法原样返回；相对路径落在 review_service/ 下
        return self._service_root / self.db_path

    @property
    def journals_seed_abs(self) -> "Path":
        return self._service_root / self.journals_seed_path

    @property
    def journal_directory_seed_abs(self) -> "Path":
        return self._service_root / self.journal_directory_seed_path

    @property
    def ppt_jobs_abs(self) -> "Path":
        p = Path(self.ppt_jobs_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def ppt_styles_abs(self) -> "Path":
        p = Path(self.ppt_styles_dir)
        return p if p.is_absolute() else self._service_root / p



@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
