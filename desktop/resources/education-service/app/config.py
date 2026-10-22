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
    recommend_read_timeout: float = 60.0    # LLM 重排读超时（秒）
    recommend_query_chars: int = 3000       # 送 vstore 的检索 query 截断（防嵌入模型超长）
    recommend_scope_top_k: int = 10         # search-chunks top_k（vstore 上限 20）

    # ---- 论文重排版（reformat，P1：.tex 源码级变换，不编译）----
    max_tex_bytes: int = 2 * 1024 * 1024    # 上传 .tex 上限 2MB（PDF 的 50MB 不适用）
    reformat_dir: str = "data/reformat_jobs"  # 产物落盘（相对 review_service/）

    # ---- LaTeX 编译 PDF（compile，P3：tectonic 单文件二进制）----
    tectonic_path: str = "tools/tectonic"       # 相对 review_service/；也可绝对路径
    tectonic_cache_dir: str = "data/tectonic-cache"  # 宏包缓存（部署时预热，运行期只读命中）
    compile_timeout_s: float = 60.0             # 单次编译墙钟上限，超时整进程组 kill
    max_compile_pdf_bytes: int = 50 * 1024 * 1024  # 产物 PDF 上限 50MB
    compile_queue_limit: int = 5                # 等待槽位上限（>5 → QUEUE_FULL 429）
    compile_retention_days: int = 7             # 编译产物保留天数（后台清扫）

    # ---- LaTeX 项目 zip 编译（P4：两段式 inspect + run）----
    max_zip_bytes: int = 50 * 1024 * 1024       # 上传 zip 上限 50MB
    max_extract_bytes: int = 200 * 1024 * 1024  # 单项目解压总大小硬顶 200MB（实写字节计数）
    max_zip_entries: int = 2000                 # 条目数上限（预扫第一件事）
    max_zip_entry_bytes: int = 50 * 1024 * 1024 # 单条目解压上限 50MB
    max_zip_ratio: float = 100.0                # 单条目压缩比 file_size/compress_size 上限
    max_zip_path_depth: int = 16                # 路径深度上限（深层嵌套拒绝）
    max_zip_path_len: int = 200                 # 单条目路径长度上限
    compile_project_timeout_s: float = 120.0    # 项目 zip 编译墙钟上限（单文件仍 compile_timeout_s）
    inspect_dir: str = "data/compile_inspect"   # zip 解压暂存（1 小时 TTL，相对 review_service/）
    inspect_ttl_s: int = 3600                   # inspect 暂存过期秒数
    inspect_sweep_interval_s: float = 300.0     # inspect 后台清扫周期（lifespan 定时任务）
    compile_jobs_dir: str = "data/compile_jobs" # 项目编译产物（pdf/log/meta，7 天 sweep）
    failed_project_ttl_s: int = 24 * 3600       # 编译失败保留 project/ 现场秒数（诊断用）
    keep_failed_projects: bool = False          # 失败 project/ 永久保留（调试开关；
                                                # 亦读裸环境变量 KEEP_FAILED_PROJECTS=1）

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
    def reformat_dir_abs(self) -> "Path":
        p = Path(self.reformat_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def ppt_jobs_abs(self) -> "Path":
        p = Path(self.ppt_jobs_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def ppt_styles_abs(self) -> "Path":
        p = Path(self.ppt_styles_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def tectonic_abs(self) -> "Path":
        p = Path(self.tectonic_path)
        return p if p.is_absolute() else self._service_root / p

    @property
    def tectonic_cache_abs(self) -> Path:
        p = Path(self.tectonic_cache_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def inspect_dir_abs(self) -> Path:
        p = Path(self.inspect_dir)
        return p if p.is_absolute() else self._service_root / p

    @property
    def compile_jobs_dir_abs(self) -> Path:
        p = Path(self.compile_jobs_dir)
        return p if p.is_absolute() else self._service_root / p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
