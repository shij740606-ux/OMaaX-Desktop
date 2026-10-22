"""main.py —— review 服务应用入口（uvicorn 入口）。

- 独立部署树 review_service/（科研投稿与智能审稿助手，端口 8100）。
- 端点：/health（GET，BFF 探活反代）+ /api/review/*（自审稿等业务路由）。
- 不依赖 BFF/生产服务（8090 等绝不探测/连接）；业务在本地运行，只有 LLM 请求使用外部 API。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import close_db, db, init_db
from .routers import (
    compile,
    conferences,
    format_check,
    journal_directory,
    journals,
    ppt,
    presubmit,
    reformat,
    rebuttal,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.version)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # journals 推荐依赖独立 SQLite：启动即建表（幂等；测试可先 set_path 注入
        # tmp 路径，本函数沿用注入路径）
        await init_db()
        from .db import ensure_journals_seeded
        await ensure_journals_seeded()
        # rebuttal Phase 1：独立表，启动幂等建表（自含连接，不走 db.py）
        try:
            from .services.rebuttal_store import ensure_table
            ensure_table()
        except Exception:  # noqa: BLE001
            logger.warning("rebuttal 建表失败（忽略）", exc_info=True)
        # P5 科研汇报：ppt_jobs 独立表，启动幂等建表
        try:
            from .services.ppt_store import ensure_table as ppt_ensure_table
            ppt_ensure_table()
        except Exception:  # noqa: BLE001
            logger.warning("ppt 建表失败（忽略）", exc_info=True)
        # User-generated outputs remain until explicit deletion in the desktop.
        # journal_directory 自动 seed：空表才导（xlsx 解析约 1-3s，不堵 event loop；
        # 路径沿用 db.path 注入链——生产=settings.db_path_abs，测试=tmp）
        if settings.seed_on_startup:
            try:
                from .services.journal_seed import ensure_journal_directory_seeded
                _t0 = time.time()
                _result = await asyncio.to_thread(
                    ensure_journal_directory_seeded, db.path)
                if _result.get("seeded"):
                    _c = _result.get("counts") or {}
                    logger.info(
                        "seeded %s rows (en=%s, zh=%s, %.1fs)",
                        _result["rows"], _c.get("en"), _c.get("zh"),
                        time.time() - _t0)
                elif _result.get("error"):
                    logger.warning(
                        "journal_directory 启动 seed 失败（忽略，不阻塞启动）: %s",
                        _result["error"])
                else:
                    logger.info("%s", _result.get("reason") or "journal_directory seed skipped")
            except Exception:  # noqa: BLE001
                logger.warning("journal_directory 启动 seed 失败（忽略，不阻塞启动）", exc_info=True)
        else:
            logger.info("journal_directory seed skipped (REVIEW_SEED_ON_STARTUP=false)")
        try:
            yield
        finally:
            await close_db()

    app.router.lifespan_context = _lifespan

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "status": "ok", "storage": "local", "version": settings.version}

    app.include_router(presubmit.router)
    app.include_router(rebuttal.router)
    app.include_router(journals.router)
    app.include_router(format_check.router)
    app.include_router(conferences.router)
    app.include_router(journal_directory.router)
    app.include_router(reformat.router)
    app.include_router(compile.router)
    app.include_router(ppt.router)
    return app


app = create_app()
