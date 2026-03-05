#!/bin/bash
# Chrome书签整理Skill - 快速调用脚本

set -e  # 遇到错误立即退出

echo "🚀 开始整理Chrome书签..."

# 配置（可根据需要修改）
BOOKMARK_FILE="${1:-data/bookmarks.html}"
OUTPUT_DIR="output"

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到Python3，请先安装Python 3.8+"
    exit 1
fi

# 检查依赖
if ! python3 -c "import bs4, aiohttp" &> /dev/null; then
    echo "⚠️  依赖未安装，正在安装..."
    pip install -r requirements.txt
fi

# 步骤1: 复制书签文件
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 步骤1/6: 复制书签文件"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/1_copy_bookmark.py

# 步骤2: 解析书签
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 步骤2/6: 解析书签"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/2_parse_bookmarks.py

# 步骤3: 获取网页信息（最耗时）
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 步骤3/6: 获取网页信息"
echo "⏱️  这是最耗时的步骤，请耐心等待..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/3_fetch_webpage_info.py

# 步骤4: 智能分类
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🏷️  步骤4/6: 智能分类"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/4_classify_bookmarks.py

# 步骤5: 聚类分析
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 步骤5/6: 聚类分析"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/5_cluster_bookmarks.py

# 步骤6: 生成HTML
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ 步骤6/6: 生成HTML"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 scripts/6_generate_html.py

# 完成
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 整理完成！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📄 输出文件: $OUTPUT_DIR/organized_bookmarks.html"
echo ""
echo "📋 下一步操作："
echo "   1. 打开Chrome浏览器"
echo "   2. 访问 chrome://bookmarks/"
echo "   3. 点击右上角'⋮'菜单"
echo "   4. 选择'导入书签'"
echo "   5. 选择文件: $OUTPUT_DIR/organized_bookmarks.html"
echo ""
echo "🎉 享受整洁的书签体验！"
echo ""
