#!/bin/bash
# Chrome书签整理工具 - 一键运行脚本

set -euo pipefail

BOOKMARK_FILE="${1:-data/bookmarks.html}"
CONFIG_FILE="${2:-skill_config.json}"
OUTPUT_HTML="${OUTPUT_HTML:-output/organized_bookmarks.html}"
FETCH_ARGS=("${@:3}")

mkdir -p data output logs output/reports

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 错误: 未找到 Python3，请先安装 Python 3.8+"
  exit 1
fi

if ! python3 -c "import bs4, aiohttp" >/dev/null 2>&1; then
  echo "⚠️  依赖未安装，正在安装..."
  python3 -m pip install -r requirements.txt
fi

echo "🚀 开始整理 Chrome 书签..."
echo "📄 输入文件: ${BOOKMARK_FILE}"
echo "⚙️  配置文件: ${CONFIG_FILE}"

python3 scripts/1_copy_bookmark.py --config "${CONFIG_FILE}" --source "${BOOKMARK_FILE}"
python3 scripts/2_parse_bookmarks.py --config "${CONFIG_FILE}"
python3 scripts/3_fetch_webpage_info.py --config "${CONFIG_FILE}" "${FETCH_ARGS[@]}"
python3 scripts/4_classify_bookmarks.py --config "${CONFIG_FILE}"
python3 scripts/5_cluster_bookmarks.py --config "${CONFIG_FILE}"
python3 scripts/6_generate_html.py --config "${CONFIG_FILE}" --output "${OUTPUT_HTML}"

echo "✅ 整理完成！"
echo "📄 输出文件: ${OUTPUT_HTML}"
echo "📋 重复 URL 报告: output/reports/duplicates.json"
echo "📋 失效链接报告: output/reports/broken_links.json"
echo "📋 待确认报告: output/reports/needs_confirmation.json"
