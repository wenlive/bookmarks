#!/usr/bin/env python3
"""Reset generated pipeline state."""
from __future__ import annotations

import shutil
from pathlib import Path

from common import (
    build_parser,
    configure_logging,
    load_config_from_args,
    pipeline_generated_paths,
)


def _remove_path(path: Path, logger) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    logger.info("已删除: %s", path)
    return True


def clear_fetch_cache(config, logger, *, protect: set[Path] | None = None) -> int:
    protect = protect or set()
    target = config.paths.enriched_file.resolve()
    if target in protect:
        logger.info("跳过清理受保护的抓取缓存: %s", target)
        return 0
    return int(_remove_path(target, logger))


def reset_pipeline_outputs(config, logger, *, protect: set[Path] | None = None) -> int:
    protect = {path.resolve() for path in (protect or set())}
    removed = 0
    targets = pipeline_generated_paths(config)

    for directory in targets["directories"]:
        resolved = directory.resolve()
        if resolved in protect:
            logger.info("跳过清理受保护目录: %s", resolved)
            continue
        removed += int(_remove_path(resolved, logger))

    for file_path in targets["files"]:
        resolved = file_path.resolve()
        if resolved in protect:
            logger.info("跳过清理受保护文件: %s", resolved)
            continue
        removed += int(_remove_path(resolved, logger))

    return removed


def main() -> int:
    parser = build_parser("清理书签整理流水线生成产物")
    parser.add_argument("--clear-fetch-cache", action="store_true", help="仅删除抓取缓存")
    parser.add_argument("--reset-all", action="store_true", help="删除全部中间产物和输出")
    parser.add_argument("--source", type=Path, default=None, help="受保护的原始书签文件")
    args = parser.parse_args()

    if not args.clear_fetch_cache and not args.reset_all:
        parser.error("请至少选择 --clear-fetch-cache 或 --reset-all")

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    protect = set()
    if args.source is not None:
        protect.add(args.source.resolve())

    removed = 0
    if args.clear_fetch_cache:
        removed += clear_fetch_cache(config, logger, protect=protect)
    if args.reset_all:
        removed += reset_pipeline_outputs(config, logger, protect=protect)

    print(f"✓ 清理完成，删除项: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
