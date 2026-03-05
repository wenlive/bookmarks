#!/usr/bin/env python3
"""步骤1: 复制书签文件到项目目录"""
import shutil
from pathlib import Path

def main():
    source = Path("/Users/lipoqi/Desktop/bookmarks_2026_3_4.html")
    dest = Path("/Users/lipoqi/data/playground/bookmarks/data/bookmarks_2026_3_4.html")

    if not source.exists():
        print(f"错误: 源文件不存在: {source}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    print(f"✓ 书签文件已复制: {dest}")
    print(f"  文件大小: {dest.stat().st_size / 1024:.2f} KB")
if __name__ == "__main__":
    main()
