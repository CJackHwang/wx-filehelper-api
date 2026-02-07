"""
文件管理路由

- 下载目录管理
- 文件元数据查询
- 文件删除与清理
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

from config import settings

if TYPE_CHECKING:
    from processor import CommandProcessor

router = APIRouter(tags=["Files"])

# 依赖注入
_processor: "CommandProcessor | None" = None

# 下载目录缓存
_downloads_cache: dict[str, list[dict]] | None = None
_downloads_cache_time: float = 0
_downloads_cache_ttl: float = 10.0


def init(processor: "CommandProcessor"):
    """初始化路由依赖"""
    global _processor
    _processor = processor


def _get_processor() -> "CommandProcessor":
    if _processor is None:
        raise RuntimeError("Processor not initialized")
    return _processor


def _scan_downloads(include_subdirs: bool = True) -> list[dict]:
    """扫描下载目录 (带缓存)"""
    global _downloads_cache, _downloads_cache_time

    cache_key = f"subdirs_{include_subdirs}"
    now = time.time()

    if _downloads_cache is not None and cache_key in _downloads_cache:
        if (now - _downloads_cache_time) < _downloads_cache_ttl:
            return _downloads_cache[cache_key]

    files = []
    download_dir = settings.download_dir

    if include_subdirs:
        for root, _, filenames in os.walk(download_dir):
            root_path = Path(root)
            for name in filenames:
                if name.startswith("."):
                    continue
                file_path = root_path / name
                try:
                    stat_info = file_path.stat()
                    rel_path = file_path.relative_to(download_dir)
                    files.append({
                        "name": name,
                        "path": str(rel_path),
                        "size": stat_info.st_size,
                        "modified": stat_info.st_mtime,
                    })
                except OSError:
                    continue
    else:
        try:
            with os.scandir(download_dir) as entries:
                for entry in entries:
                    if entry.is_file() and not entry.name.startswith("."):
                        try:
                            stat_info = entry.stat()
                            files.append({
                                "name": entry.name,
                                "path": entry.name,
                                "size": stat_info.st_size,
                                "modified": stat_info.st_mtime,
                            })
                        except OSError:
                            continue
        except OSError:
            pass

    files.sort(key=lambda x: x["modified"], reverse=True)

    if _downloads_cache is None:
        _downloads_cache = {}
    _downloads_cache[cache_key] = files
    _downloads_cache_time = now

    return files


def invalidate_downloads_cache():
    """使下载目录缓存失效"""
    global _downloads_cache
    _downloads_cache = None


@router.get("/downloads")
async def list_downloads(
    limit: int = Query(default=100, ge=1, le=1000),
    include_subdirs: bool = Query(default=True),
) -> dict[str, Any]:
    """列出下载的文件"""
    files = _scan_downloads(include_subdirs)
    return {
        "files": files[:limit],
        "total": len(files),
        "base_url": "/static/",
    }


@router.get("/files/metadata")
async def get_files_metadata(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """获取文件元数据 (从数据库)"""
    processor = _get_processor()
    files = processor.message_store.get_files(limit=limit, offset=offset)
    return {
        "files": [asdict(f) for f in files],
        "count": len(files),
    }


@router.delete("/files/{msg_id}")
async def delete_file(msg_id: str) -> dict[str, str]:
    """删除文件"""
    processor = _get_processor()
    file_info = processor.message_store.get_file_by_msg_id(msg_id)

    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        path = Path(file_info.file_path)
        if path.exists():
            path.unlink()
        invalidate_downloads_cache()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")

    return {"status": "deleted", "msg_id": msg_id}


@router.post("/files/cleanup")
async def cleanup_files(days: int = Query(default=30, ge=1)) -> dict[str, int]:
    """清理过期文件"""
    processor = _get_processor()
    deleted_messages = processor.message_store.cleanup_old_messages(days=days)
    deleted_files = processor.message_store.cleanup_old_files(days=days, delete_files=True)
    invalidate_downloads_cache()
    return {
        "deleted_messages": deleted_messages,
        "deleted_files": deleted_files,
    }


@router.get("/store/stats")
async def store_stats() -> dict[str, Any]:
    """获取消息存储统计"""
    processor = _get_processor()
    return processor.message_store.get_stats()


@router.get("/store/messages")
async def store_messages(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    msg_type: str | None = Query(default=None),
    since: int | None = Query(default=None, description="Unix timestamp"),
) -> dict[str, Any]:
    """查询历史消息"""
    processor = _get_processor()
    messages = processor.message_store.get_updates(
        offset=offset,
        limit=limit,
        msg_type=msg_type,
        since=since,
    )
    return {
        "messages": [asdict(m) for m in messages],
        "count": len(messages),
    }
