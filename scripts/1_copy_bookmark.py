#!/usr/bin/env python3
"""步骤1: 复制书签文件到项目目录。"""
from __future__ import annotations

import shutil
from pathlib import Path

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


def copy_bookmark_file(source: Path, destination: Path) -> Path:
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if not source_resolved.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")
    if source_resolved == destination_resolved:
        return destination_resolved

    ensure_parent(destination_resolved)
    shutil.copy2(source_resolved, destination_resolved)
    return destination_resolved


def main() -> int:
    parser = build_parser("复制原始书签文件到项目目录")
    parser.add_argument("--source", type=Path, default=None, help="原始书签 HTML 文件")
    parser.add_argument("--dest", type=Path, default=None, help="复制后的项目内 HTML 路径")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)

    source = args.source or config.paths.bookmark_file
    dest = args.dest or config.paths.copied_bookmark_file

    try:
        copied = copy_bookmark_file(source, dest)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        print(f"错误: {exc}")
        return 1

    logger.info("步骤1完成: %s -> %s", source, copied)
    print(f"✓ 书签文件已复制: {copied}")
    print(f"  文件大小: {copied.stat().st_size / 1024:.2f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
