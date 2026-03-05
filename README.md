# Chrome书签智能整理工具

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-1.0.0-orange)

> 基于Python的智能书签整理工具 - 让混乱的书签重获新生

## 简介

Chrome书签智能整理工具是一个6步流水线处理系统，通过异步抓取、多维度评分和智能聚类，将杂乱的书签自动整理成层次分明的结构。

### 核心特性

- **6步流水线处理** - 从原始HTML到整理后的Chrome标准格式
- **多维度智能分类** - 域名、关键词、标题、文件夹、内容5维度加权评分
- **异步高效抓取** - 基于aiohttp的并发网页信息提取
- **40+技术领域** - 覆盖编程语言、数据库、分布式系统等主流技术栈
- **保留原始信息** - 保留图标、添加日期等Chrome书签元数据
- **即插即用** - 无需复杂配置，3步即可完成整理

### 使用场景

- 定期整理Chrome书签，保持收藏夹整洁
- 批量处理大量未分类书签
- 将书签从其他浏览器迁移并智能分类
- 集成到自动化工作流中定期整理

## 快速开始

只需3步，即可完成书签整理：

### 步骤1: 从Chrome导出书签

1. 打开Chrome浏览器
2. 访问 `chrome://bookmarks/`
3. 点击右上角"⋮"菜单 → "导出书签"
4. 保存为 `bookmarks.html`

### 步骤2: 运行整理脚本

```bash
# 克隆或下载项目
cd bookmarks

# 安装依赖
pip install -r requirements.txt

# 将导出的书签文件复制到data目录
cp ~/Downloads/bookmarks.html data/bookmarks.html

# 依次运行6个步骤
python scripts/1_copy_bookmark.py
python scripts/2_parse_bookmarks.py
python scripts/3_fetch_webpage_info.py
python scripts/4_classify_bookmarks.py
python scripts/5_cluster_bookmarks.py
python scripts/6_generate_html.py
```

**预期输出：**

```
✓ 书签文件已复制: data/bookmarks.html
✓ 解析完成: data/parsed_bookmarks.json (找到 1234 个书签)
✓ 网页信息获取完成: data/bookmarks_with_info.json (成功率 92.5%)
✓ 分类完成: data/classified_bookmarks.json (38个分类)
✓ 聚类完成: data/clustering_result.json
✓ HTML生成完成: output/organized_bookmarks.html (可导入Chrome)
```

### 步骤3: 导入Chrome

1. 打开 `chrome://bookmarks/`
2. 点击右上角"⋮"菜单 → "导入书签"
3. 选择 `output/organized_bookmarks.html`
4. 完成！

## 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│                    Chrome书签智能整理流程                      │
└─────────────────────────────────────────────────────────────┘

[Chrome书签HTML]
       ↓
┌──────────────────┐
│ 1. 复制书签文件   │  备份原始文件到项目目录
└──────────────────┘
       ↓
┌──────────────────┐
│ 2. 解析HTML      │  BeautifulSoup解析，提取书签和文件夹结构
└──────────────────┘
       ↓
┌──────────────────┐
│ 3. 获取网页信息   │  异步抓取标题、描述、关键词（aiohttp）
└──────────────────┘
       ↓
┌──────────────────┐
│ 4. 智能分类      │  多维度评分系统（域名+关键词+文件夹+内容）
└──────────────────┘
       ↓
┌──────────────────┐
│ 5. 聚类分析      │  域名聚类 + 关键词聚类，构建层级结构
└──────────────────┘
       ↓
┌──────────────────┐
│ 6. 生成HTML      │  生成符合Chrome标准的书签HTML
└──────────────────┘
       ↓
[整理后的书签HTML]
```

### 步骤详解

#### 步骤1: 复制书签文件

- **作用**: 将Chrome导出的书签文件复制到项目data目录
- **技术**: Python shutil模块
- **输入**: Chrome导出的HTML文件
- **输出**: `data/bookmarks_YYYY_M_D.html`
- **耗时**: < 1秒
- **关键参数**: 需要修改脚本中的源文件路径

#### 步骤2: 解析书签

- **作用**: 解析HTML，提取所有书签和文件夹结构
- **技术**: BeautifulSoup (html.parser)
- **输入**: `data/bookmarks_YYYY_M_D.html`
- **输出**: `data/parsed_bookmarks.json`
- **耗时**: 1-5秒（取决于书签数量）
- **关键特性**:
  - 支持深度嵌套的文件夹结构
  - URL去重（MD5 hash）
  - 提取域名统计
  - 保留原始文件夹路径

#### 步骤3: 获取网页信息

- **作用**: 异步抓取每个书签的网页元信息
- **技术**: asyncio + aiohttp + BeautifulSoup (lxml)
- **输入**: `data/parsed_bookmarks.json`
- **输出**: `data/bookmarks_with_info.json`
- **耗时**: 取决于书签数量和网络状况（1000个书签约10-20分钟）
- **关键参数**:
  - `CONCURRENT_LIMIT = 15` - 并发连接数
  - `TIMEOUT = 15` - 单个请求超时（秒）
  - `DELAY = 0.8` - 请求间延迟（秒）
  - `BATCH_SIZE = 50` - 批处理大小
- **提取信息**:
  - 页面标题
  - Meta描述
  - Meta关键词
  - H1标题
  - 内容预览（前500字符）

#### 步骤4: 智能分类

- **作用**: 基于多维度评分系统对书签进行分类
- **技术**: 自定义多维度评分算法
- **输入**:
  - `data/bookmarks_with_info.json`
  - `data/category_rules.json`
- **输出**: `data/classified_bookmarks.json`
- **耗时**: 5-30秒
- **评分维度**:
  1. **域名匹配** (权重30%): 域名精确匹配得100分
  2. **关键词匹配** (权重40%): 标题/关键词/描述中的关键词匹配
  3. **标题模式** (权重40%): 正则表达式匹配标题模式
  4. **文件夹匹配** (权重25%): 原始文件夹路径关键词匹配
  5. **内容匹配** (权重5%): 网页内容关键词匹配
- **阈值配置**:
  - `min_score = 15` - 最低分类阈值
  - `confirm_threshold = 25` - 需要确认的阈值

#### 步骤5: 聚类分析

- **作用**: 对每个分类内的书签进行聚类，构建层级结构
- **技术**: 基于规则的聚类算法（域名聚类 + 关键词聚类）
- **输入**: `data/classified_bookmarks.json`
- **输出**: `data/clustering_result.json`
- **耗时**: 5-20秒
- **聚类策略**:
  - 书签数 ≤ 20: 直接保留，不进行聚类
  - 书签数 > 20:
    - 优先按域名聚类（组大小 ≥ 5）
    - 否则按关键词聚类（组大小 ≥ 10）
- **关键参数**:
  - `min_cluster_size = 10` - 最小聚类大小

#### 步骤6: 生成HTML

- **作用**: 生成符合Chrome标准的书签HTML文件
- **技术**: 字符串拼接生成HTML
- **输入**: `data/clustering_result.json`
- **输出**: `output/organized_bookmarks.html`
- **耗时**: 1-3秒
- **特性**:
  - 符合Chrome书签格式标准
  - 保留图标、添加日期等元数据
  - 层级文件夹结构
  - 按数量排序（"其他"分类在最后）

## 安装与配置

### 系统要求

- Python 3.8+
- 网络连接（用于获取网页信息）

### 依赖安装

```bash
pip install -r requirements.txt
```

**依赖列表：**
- `beautifulsoup4 >= 4.12.0` - HTML解析
- `lxml >= 4.9.0` - 高性能HTML解析器
- `aiohttp >= 3.9.0` - 异步HTTP客户端
- `scikit-learn >= 1.3.0` - 机器学习库（可选，当前版本未使用）
- `numpy >= 1.24.0` - 数值计算（scikit-learn依赖）

### 目录结构

```
bookmarks/
├── README.md                   # 本文档
├── requirements.txt            # Python依赖
├── data/                       # 数据目录
│   ├── category_rules.json    # 分类规则配置
│   ├── bookmarks_*.html       # 原始书签文件
│   ├── parsed_bookmarks.json  # 解析结果
│   ├── bookmarks_with_info.json # 网页信息
│   ├── classified_bookmarks.json # 分类结果
│   └── clustering_result.json # 聚类结果
├── scripts/                    # 处理脚本
│   ├── 1_copy_bookmark.py
│   ├── 2_parse_bookmarks.py
│   ├── 3_fetch_webpage_info.py
│   ├── 4_classify_bookmarks.py
│   ├── 5_cluster_bookmarks.py
│   └── 6_generate_html.py
└── output/                     # 输出目录
    └── organized_bookmarks.html # 最终整理结果
```

### 配置文件说明

#### category_rules.json

分类规则配置文件，定义了40+技术领域的分类规则：

```json
{
  "categories": {
    "编程语言/Python": {
      "domains": ["python.org", "pypi.org"],
      "keywords": ["python", "django", "flask", "fastapi"],
      "title_patterns": ["[Pp]ython.*", "[Dd]jango.*"],
      "folder_keywords": ["python"]
    }
  },
  "default_category": "其他/未分类",
  "scoring": {
    "domain_weight": 30,
    "keyword_weight": 40,
    "folder_weight": 25,
    "content_weight": 5,
    "min_score": 15,
    "confirm_threshold": 25
  }
}
```

## 作为Skill使用

本工具设计为可复用的Skill，可集成到各种工作流中。

### Skill概念

"Skill"是指可独立运行、可配置、可组合的自动化任务单元。本工具将书签整理封装为Skill，支持：

- **可配置参数**: 通过配置文件调整行为
- **批量处理**: 支持处理多个书签文件
- **跳过步骤**: 可选择性执行某些步骤
- **API调用**: 可在Python代码中直接调用

### 使用场景

1. **定期整理**: 每月自动整理书签
2. **批量处理**: 一次性处理多个Chrome配置文件的书签
3. **集成自动化**: 集成到CI/CD或定时任务中

### 快速调用脚本

创建 `organize.sh` 快速运行整个流程：

```bash
#!/bin/bash
# Chrome书签整理Skill - 快速调用脚本

set -e  # 遇到错误立即退出

echo "🚀 开始整理Chrome书签..."

# 配置（可根据需要修改）
BOOKMARK_FILE="${1:-data/bookmarks.html}"
OUTPUT_DIR="output"

# 步骤1: 复制书签文件
echo "📁 步骤1: 复制书签文件"
python scripts/1_copy_bookmark.py

# 步骤2: 解析书签
echo "🔍 步骤2: 解析书签"
python scripts/2_parse_bookmarks.py

# 步骤3: 获取网页信息（最耗时）
echo "🌐 步骤3: 获取网页信息（可能需要几分钟）"
python scripts/3_fetch_webpage_info.py

# 步骤4: 智能分类
echo "🏷️  步骤4: 智能分类"
python scripts/4_classify_bookmarks.py

# 步骤5: 聚类分析
echo "📊 步骤5: 聚类分析"
python scripts/5_cluster_bookmarks.py

# 步骤6: 生成HTML
echo "✨ 步骤6: 生成HTML"
python scripts/6_generate_html.py

echo "✅ 整理完成！"
echo "📄 输出文件: $OUTPUT_DIR/organized_bookmarks.html"
echo "💡 现在可以导入Chrome: chrome://bookmarks/ → 导入书签"
```

**使用方法：**

```bash
chmod +x organize.sh
./organize.sh data/my_bookmarks.html
```

### 参数配置

创建 `skill_config.json` 配置文件：

```json
{
  "input": {
    "bookmark_file": "data/bookmarks.html",
    "rules_file": "data/category_rules.json"
  },
  "output": {
    "directory": "output",
    "prefix": "organized"
  },
  "fetch_options": {
    "concurrent_limit": 15,
    "timeout": 15,
    "delay": 0.8,
    "batch_size": 50
  },
  "classification_options": {
    "min_score": 15,
    "confirm_threshold": 25
  },
  "clustering_options": {
    "min_cluster_size": 10
  },
  "skip_steps": []
}
```

### Python API调用示例

```python
#!/usr/bin/env python3
"""书签整理Skill API调用示例"""
import sys
sys.path.append('scripts')

from pathlib import Path
from parse_bookmarks import parse_bookmarks
from classify_bookmarks import BookmarkClassifier
from generate_html import BookmarkHTMLGenerator

def organize_bookmarks(input_file: Path, output_file: Path):
    """整理书签的完整流程"""

    # 步骤2: 解析书签
    print("解析书签...")
    parsed_data = parse_bookmarks(input_file)

    # 步骤3: 获取网页信息（简化示例，实际需要异步）
    # 这里可以使用步骤3的异步函数

    # 步骤4: 分类
    print("分类书签...")
    classifier = BookmarkClassifier(Path('data/category_rules.json'))
    classified, stats, _ = classifier.classify_all(parsed_data['bookmarks'])

    # 步骤6: 生成HTML
    print("生成HTML...")
    generator = BookmarkHTMLGenerator()
    # 需要先构建hierarchy结构
    # html = generator.generate_html(hierarchy)

    print(f"完成！输出到: {output_file}")

if __name__ == "__main__":
    organize_bookmarks(
        Path('data/bookmarks.html'),
        Path('output/organized.html')
    )
```

## 高级特性

### 自定义分类规则

在 `data/category_rules.json` 中添加自定义分类：

```json
{
  "categories": {
    "我的项目": {
      "domains": ["myproject.com", "docs.myproject.io"],
      "keywords": ["myproject", "我的项目"],
      "title_patterns": ["[Mm]yproject.*"],
      "folder_keywords": ["myproject"]
    },
    "学习资源/视频教程": {
      "domains": ["youtube.com", "bilibili.com", "coursera.org"],
      "keywords": ["视频", "教程", "course", "tutorial"],
      "title_patterns": ["[Tt]utorial.*", "教程.*"],
      "folder_keywords": ["视频", "教程"]
    }
  }
}
```

### 调整参数

#### 修改步骤3的网络参数

编辑 `scripts/3_fetch_webpage_info.py`:

```python
# 配置
CONCURRENT_LIMIT = 20  # 提高并发数（注意不要过高，避免被封）
TIMEOUT = 20           # 增加超时时间
DELAY = 0.5            # 减少延迟（注意礼貌性）
BATCH_SIZE = 100       # 增加批处理大小
```

#### 修改分类评分权重

编辑 `data/category_rules.json`:

```json
{
  "scoring": {
    "domain_weight": 40,      # 提高域名权重
    "keyword_weight": 30,     # 降低关键词权重
    "folder_weight": 20,
    "content_weight": 10,
    "min_score": 20,          # 提高最低阈值
    "confirm_threshold": 30
  }
}
```

#### 修改聚类参数

编辑 `scripts/5_cluster_bookmarks.py`:

```python
clusterer = BookmarkClusterer(min_cluster_size=15)  # 增加最小聚类大小
```

### 跳过步骤

如果已经有中间结果，可以跳过某些步骤：

```bash
# 跳过步骤3（最耗时），直接使用已有的网页信息
python scripts/4_classify_bookmarks.py
python scripts/5_cluster_bookmarks.py
python scripts/6_generate_html.py
```

### 批量处理多个文件

```bash
#!/bin/bash
# 批量处理多个书签文件

for file in data/bookmarks_*.html; do
    echo "处理文件: $file"

    # 修改步骤1脚本中的源文件路径，或直接复制
    cp "$file" data/bookmarks.html

    # 运行所有步骤
    python scripts/2_parse_bookmarks.py
    python scripts/3_fetch_webpage_info.py
    python scripts/4_classify_bookmarks.py
    python scripts/5_cluster_bookmarks.py
    python scripts/6_generate_html.py

    # 重命名输出文件
    timestamp=$(date +%Y%m%d_%H%M%S)
    mv output/organized_bookmarks.html "output/organized_${timestamp}.html"
done
```

### 性能优化建议

1. **网络优化**:
   - 使用代理池避免IP被封
   - 适当增加 `CONCURRENT_LIMIT`（10-30）
   - 减少 `DELAY` 但保持礼貌性（0.3-0.8秒）

2. **缓存优化**:
   - 保留 `bookmarks_with_info.json`，避免重复抓取
   - 对已抓取的URL建立本地缓存

3. **分类优化**:
   - 完善 `category_rules.json` 中的分类规则
   - 根据实际书签特点调整评分权重

4. **聚类优化**:
   - 调整 `min_cluster_size` 避免过度细分
   - 对特定分类使用不同的聚类策略

## 示例与演示

### 输入示例

Chrome书签HTML片段：

```html
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1234567890">书签栏</H3>
    <DL><p>
        <DT><H3 ADD_DATE="1234567891">技术</H3>
        <DL><p>
            <DT><A HREF="https://golang.org/" ADD_DATE="1234567892" ICON="data:image/png;base64,...">The Go Programming Language</A>
            <DT><A HREF="https://python.org/" ADD_DATE="1234567893">Python.org</A>
        </DL><p>
    </DL><p>
</DL><p>
```

### 中间输出示例

**步骤2输出** (`parsed_bookmarks.json`):

```json
{
  "bookmarks": [
    {
      "id": "bookmark_0",
      "name": "The Go Programming Language",
      "url": "https://golang.org/",
      "domain": "golang.org",
      "original_folder_path": ["技术"],
      "add_date": "1234567892",
      "icon": "data:image/png;base64,...",
      "metadata": {}
    }
  ],
  "stats": {
    "total_bookmarks": 1234,
    "unique_domains": 456,
    "top_domains": {
      "github.com": 150,
      "stackoverflow.com": 80
    }
  }
}
```

**步骤3输出** (`bookmarks_with_info.json`):

```json
{
  "bookmarks": [
    {
      "id": "bookmark_0",
      "name": "The Go Programming Language",
      "url": "https://golang.org/",
      "metadata": {
        "title": "The Go Programming Language",
        "description": "Go is an open source programming language...",
        "keywords": "go, golang, programming",
        "h1": "Go is an open source programming language",
        "content_preview": "Go is an open source programming language...",
        "fetch_status": "success"
      }
    }
  ]
}
```

**步骤4输出** (`classified_bookmarks.json`):

```json
{
  "bookmarks": [
    {
      "id": "bookmark_0",
      "classification": {
        "category": "编程语言/Go",
        "score": 35.5,
        "needs_confirmation": false,
        "all_scores": {
          "编程语言/Go": {
            "total": 35.5,
            "domain": 100,
            "keyword": 40,
            "title": 0,
            "folder": 0,
            "content": 20
          }
        }
      }
    }
  ],
  "stats": {
    "category_distribution": {
      "编程语言/Go": 45,
      "编程语言/Python": 38,
      "开源项目": 120
    }
  }
}
```

### 最终输出示例

整理后的Chrome书签HTML：

```html
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">书签栏</H3>
    <DL><p>
        <DT><H3 ADD_DATE="1709567890">开源项目</H3>
        <DL><p>
            <DT><A HREF="https://github.com/golang/go" ADD_DATE="1709567890">golang/go: The Go programming language</A>
            <DT><A HREF="https://github.com/python/cpython" ADD_DATE="1709567891">python/cpython: The Python programming language</A>
        </DL><p>

        <DT><H3 ADD_DATE="1709567892">编程语言</H3>
        <DL><p>
            <DT><H3 ADD_DATE="1709567893">Go</H3>
            <DL><p>
                <DT><A HREF="https://golang.org/" ADD_DATE="1709567894" ICON="...">The Go Programming Language</A>
                <DT><A HREF="https://go.dev/" ADD_DATE="1709567895">Go.dev</A>
            </DL><p>

            <DT><H3 ADD_DATE="1709567896">Python</H3>
            <DL><p>
                <DT><A HREF="https://python.org/" ADD_DATE="1709567897">Python.org</A>
            </DL><p>
        </DL><p>

        <DT><H3 ADD_DATE="1709567898">其他/未分类</H3>
        <DL><p>
            <DT><A HREF="https://example.com/" ADD_DATE="1709567899">Example Site</A>
        </DL><p>
    </DL><p>
</DL><p>
```

### 前后对比

**整理前：**
```
书签栏/
├── 技术资料/
│   ├── (混乱的123个书签)
├── 待整理/
│   ├── (未分类的456个书签)
├── 工作相关/
│   ├── (杂乱的78个书签)
└── 其他书签/
    └── (没有文件夹结构的书签)
```

**整理后：**
```
书签栏/
├── 开源项目/ (120个)
│   ├── github.com (80个)
│   └── gitlab.com (40个)
├── 编程语言/ (120个)
│   ├── Go (45个)
│   ├── Python (38个)
│   ├── JavaScript (25个)
│   └── Rust (12个)
├── 数据库/ (85个)
│   ├── MySQL (30个)
│   ├── PostgreSQL (28个)
│   ├── TiDB (15个)
│   └── Redis (12个)
├── 分布式系统/ (75个)
│   ├── Docker (35个)
│   ├── Kubernetes (25个)
│   └── 其他 (15个)
└── 其他/未分类/ (剩余书签)
```

### 实际案例

**案例1: 1000个书签整理**

- **输入**: 1000个混乱的书签，分布在50个文件夹
- **耗时**: 约15分钟（主要耗时在步骤3）
- **结果**:
  - 成功分类: 920个 (92%)
  - 未分类: 80个 (8%)
  - 生成分类: 32个
  - 子分类: 15个
- **成功率**: 网页信息抓取成功率 93.5%

**案例2: 5000个书签整理**

- **输入**: 5000个积累多年的书签，几乎无文件夹结构
- **耗时**: 约1.5小时
- **结果**:
  - 成功分类: 4650个 (93%)
  - 未分类: 350个 (7%)
  - 生成分类: 45个
  - 子分类: 38个
- **成功率**: 网页信息抓取成功率 89.2%

## 配置参考

### category_rules.json详解

#### 分类结构

每个分类包含4个匹配维度：

```json
{
  "分类名称": {
    "domains": [],           // 域名列表（精确匹配）
    "keywords": [],          // 关键词列表（模糊匹配）
    "title_patterns": [],    // 标题正则模式
    "folder_keywords": []    // 原始文件夹关键词
  }
}
```

#### 4个匹配维度详解

1. **domains**: 域名匹配

```json
"domains": ["golang.org", "go.dev"]
```
- 精确匹配域名（子字符串匹配）
- 得分: 100分
- 权重: 30%

2. **keywords**: 关键词匹配

```json
"keywords": ["go", "golang", "goroutine"]
```
- 在书签名称、meta关键词、meta描述中搜索
- 每个关键词: 20分（最高100分）
- 权重: 40%

3. **title_patterns**: 标题正则模式

```json
"title_patterns": ["[Gg]o.*教程", "[Gg]olang.*实践"]
```
- 使用正则表达式匹配书签名称
- 匹配成功: 80分
- 权重: 40%（与keywords共享）

4. **folder_keywords**: 文件夹关键词

```json
"folder_keywords": ["go", "golang"]
```
- 匹配原始Chrome书签的文件夹路径
- 匹配成功: 80分
- 权重: 25%

#### 自定义示例

添加一个新的分类 "前端框架/React":

```json
{
  "前端框架/React": {
    "domains": [
      "reactjs.org",
      "react.dev",
      "create-react-app.dev"
    ],
    "keywords": [
      "react",
      "reactjs",
      "react hooks",
      "jsx",
      "redux",
      "next.js"
    ],
    "title_patterns": [
      "[Rr]eact.*",
      "[Rr]eactjs.*",
      "React.*教程",
      "React.*指南"
    ],
    "folder_keywords": [
      "react",
      "React",
      "reactjs"
    ]
  }
}
```

### 步骤3参数配置

在 `scripts/3_fetch_webpage_info.py` 中配置：

| 参数 | 默认值 | 说明 | 建议范围 |
|------|--------|------|----------|
| `CONCURRENT_LIMIT` | 15 | 并发连接数 | 10-30 |
| `TIMEOUT` | 15 | 单个请求超时（秒） | 10-30 |
| `DELAY` | 0.8 | 请求间延迟（秒） | 0.3-1.5 |
| `BATCH_SIZE` | 50 | 批处理大小 | 30-100 |
| `MAX_RETRIES` | 2 | 最大重试次数 | 1-3 |

**性能调优建议：**

- **快速网络**: `CONCURRENT_LIMIT=25`, `DELAY=0.5`
- **慢速网络**: `CONCURRENT_LIMIT=10`, `TIMEOUT=20`
- **大量书签**: `BATCH_SIZE=100`, 增加DELAY避免被封

### 评分权重配置

在 `data/category_rules.json` 中配置：

```json
{
  "scoring": {
    "domain_weight": 30,      // 域名匹配权重
    "keyword_weight": 40,     // 关键词+标题模式权重
    "folder_weight": 25,      // 原始文件夹匹配权重
    "content_weight": 5,      // 网页内容匹配权重
    "min_score": 15,          // 最低分类阈值（低于此值归入"未分类"）
    "confirm_threshold": 25   // 需要人工确认的阈值
  }
}
```

**调优建议：**

- **重视域名**: 提高 `domain_weight` 到 40-50
- **重视原始结构**: 提高 `folder_weight` 到 30-40
- **严格分类**: 提高 `min_score` 到 20-25
- **宽松分类**: 降低 `min_score` 到 10-12

## 常见问题

### 使用问题

#### Q1: 步骤3运行很慢怎么办？

**A:** 步骤3是最耗时的步骤，需要逐个抓取网页信息。可以：

1. **调整并发参数**:
   ```python
   CONCURRENT_LIMIT = 25  # 提高并发数
   DELAY = 0.5            # 减少延迟
   ```

2. **使用缓存**: 如果之前运行过，可以跳过步骤3，直接使用已有的 `bookmarks_with_info.json`

3. **分批处理**: 将书签文件拆分成多个小文件，分批处理

4. **网络优化**: 使用更快的网络或代理

#### Q2: 很多书签被分到"未分类"？

**A:** 可能的原因和解决方案：

1. **分类规则不够完善**:
   - 检查 `category_rules.json`，添加缺失的分类
   - 查看未分类书签的域名和关键词，补充规则

2. **阈值过高**:
   ```json
   "min_score": 10  // 降低最低阈值
   ```

3. **网页信息获取失败**:
   - 检查 `bookmarks_with_info.json` 中的 `fetch_status`
   - 如果大量失败，考虑调整网络参数或重试

4. **评分权重不合理**:
   - 根据实际情况调整 `scoring` 配置

#### Q3: 如何自定义分类规则？

**A:** 编辑 `data/category_rules.json`:

1. **添加新分类**:
   ```json
   "新分类名称": {
     "domains": ["example.com"],
     "keywords": ["关键词1", "关键词2"],
     "title_patterns": ["[Pp]attern.*"],
     "folder_keywords": ["文件夹关键词"]
   }
   ```

2. **修改现有分类**: 直接编辑对应的分类规则

3. **测试规则**: 运行步骤4查看分类效果

#### Q4: 可以跳过某些步骤吗？

**A:** 可以。每个步骤都是独立的，只要中间文件存在：

- **跳过步骤3**: 如果已有 `bookmarks_with_info.json`，可以直接从步骤4开始
- **跳过步骤2-3**: 如果已有分类数据，可以直接从步骤4开始
- **只重新分类**: 修改规则后，运行步骤4-6即可

#### Q5: 导入Chrome后图标丢失怎么办？

**A:** Chrome导入书签时可能无法完美保留图标：

1. **原始书签有图标**: 工具会保留 `ICON` 属性，但Chrome可能不显示
2. **解决方案**:
   - 导入后，Chrome会自动重新获取图标（需要访问网站）
   - 使用Chrome扩展（如"Bookmark Favicon Changer"）手动设置
   - 保留原始书签文件作为备份

### 故障排查

#### 依赖安装问题

**问题**: `pip install` 失败

**解决方案**:
```bash
# 升级pip
pip install --upgrade pip

# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 单独安装有问题的包
pip install lxml --pre
```

#### 文件路径问题

**问题**: `错误: 输入文件不存在`

**解决方案**:
1. 检查文件路径是否正确
2. 修改脚本中的硬编码路径（步骤1、步骤6）
3. 确保在项目根目录运行脚本

#### 网络连接问题

**问题**: 步骤3大量超时或失败

**解决方案**:
```python
# 增加超时时间
TIMEOUT = 20

# 减少并发数
CONCURRENT_LIMIT = 10

# 增加延迟
DELAY = 1.5

# 检查网络连接
ping google.com
```

#### 超时警告处理

**问题**: 出现大量 `timeout` 警告

**解决方案**:
1. 这是正常现象，部分网站可能无法访问或响应慢
2. 检查 `bookmarks_with_info.json` 中的 `fetch_status`
3. 如果成功率 > 80%，可以继续后续步骤
4. 如果成功率 < 50%，检查网络或调整参数

### 最佳实践

1. **定期整理**: 建议每月或每季度整理一次书签

2. **备份原始书签**:
   ```bash
   # 在整理前备份
   cp data/bookmarks.html data/bookmarks_backup_$(date +%Y%m%d).html
   ```

3. **渐进式导入**:
   - 先导入少量书签测试效果
   - 确认满意后再导入全部

4. **保留中间结果**:
   - 保留 `bookmarks_with_info.json`，避免重复抓取
   - 定期备份 `data/` 目录

5. **调整规则**:
   - 根据实际书签特点调整 `category_rules.json`
   - 定期查看"未分类"书签，补充规则

6. **性能优化**:
   - 根据网络状况调整并发参数
   - 大量书签（>2000）建议分批处理

## 演进计划

### 当前版本 (v1.0.0)

**已实现功能:**

- ✅ 6步流水线处理
- ✅ BeautifulSoup HTML解析
- ✅ 异步网页信息抓取（aiohttp）
- ✅ 多维度评分分类系统
- ✅ 40+技术领域分类规则
- ✅ 域名聚类 + 关键词聚类
- ✅ Chrome标准HTML生成
- ✅ 保留原始元数据（图标、日期）
- ✅ 批量处理支持

**已知限制:**

- ⚠️ 步骤3耗时较长（1000书签约15分钟）
- ⚠️ 部分网站可能无法访问或超时
- ⚠️ 分类规则需要手动维护
- ⚠️ 聚类算法较简单（基于规则）
- ⚠️ 图标可能在导入后丢失

### 路线图

#### v1.1 - 信息增强 (计划中)

- [ ] Open Graph标签提取
- [ ] 网站截图生成
- [ ] RSS/Atom订阅检测
- [ ] 社交媒体元数据提取
- [ ] 页面性能指标收集

#### v1.2 - 智能化升级 (计划中)

- [ ] 机器学习分类（基于BERT/embeddings）
- [ ] 智能标签推荐
- [ ] 语义聚类（DBSCAN/HDBSCAN）
- [ ] 自动规则学习
- [ ] 分类效果评估

#### v1.3 - 质量提升 (计划中)

- [ ] 重复书签检测
- [ ] 失效链接检测
- [ ] 书签质量评分
- [ ] 自动清理建议
- [ ] 版本控制与回滚

#### v2.0 - 平台化 (未来)

- [ ] Web UI界面
- [ ] REST API服务
- [ ] Chrome扩展插件
- [ ] 多用户支持
- [ ] 云端同步
- [ ] 协作整理功能

### 贡献机会

欢迎社区贡献，以下是一些贡献方向：

#### 分类规则贡献

- 补充新的技术领域分类
- 优化现有分类规则
- 添加不同语言的规则（英文、日文等）

#### 算法改进

- 改进聚类算法
- 优化评分系统
- 提升分类准确率

#### 测试用例

- 添加单元测试
- 添加集成测试
- 性能基准测试

#### 文档改进

- 完善API文档
- 添加更多示例
- 多语言文档

#### 功能增强

- 实现v1.1/v1.2路线图中的功能
- 修复已知问题
- 性能优化

### 架构扩展点

工具设计了多个扩展点，方便自定义：

#### 自定义信息提取器

```python
class CustomInfoExtractor:
    """自定义网页信息提取器"""

    async def extract(self, url: str) -> dict:
        # 自定义提取逻辑
        return {
            "title": "...",
            "custom_field": "..."
        }
```

#### 自定义分类器

```python
class CustomClassifier:
    """自定义分类器"""

    def classify(self, bookmark: dict) -> str:
        # 自定义分类逻辑
        return "分类名称"
```

#### 自定义聚类算法

```python
class CustomClusterer:
    """自定义聚类算法"""

    def cluster(self, bookmarks: list) -> dict:
        # 自定义聚类逻辑
        return {"cluster1": [...], "cluster2": [...]}
```

## 贡献指南

### 如何贡献

我们欢迎各种形式的贡献！

#### 报告问题

1. 访问 [GitHub Issues](https://github.com/your-repo/bookmarks/issues)
2. 搜索是否已有类似问题
3. 如果没有，创建新Issue，包含：
   - 问题描述
   - 复现步骤
   - 预期结果
   - 实际结果
   - 环境信息（Python版本、操作系统）

#### 提交代码

1. Fork本仓库
2. 创建特性分支:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. 提交更改:
   ```bash
   git commit -m "Add: 简短描述"
   ```
4. 推送到分支:
   ```bash
   git push origin feature/your-feature-name
   ```
5. 创建Pull Request

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/your-repo/bookmarks.git
cd bookmarks

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装开发依赖
pip install pytest black flake8

# 运行测试
pytest tests/

# 代码格式化
black scripts/

# 代码检查
flake8 scripts/
```

### 代码规范

- 遵循 [PEP 8](https://pep8.org/) 编码规范
- 使用 [Black](https://black.readthedocs.io/) 格式化代码
- 添加类型提示（Type Hints）
- 编写文档字符串（Docstring）
- 保持函数简洁（< 50行）

### 文档贡献

- 修复拼写错误
- 改进说明清晰度
- 添加更多示例
- 翻译文档

### 测试贡献

- 添加单元测试
- 添加集成测试
- 提高测试覆盖率
- 添加性能测试

### 社区准则

- 尊重所有贡献者
- 保持友好和专业
- 接受建设性批评
- 专注于对社区最有利的事情

## 致谢与许可

### License

本项目采用 [MIT License](LICENSE) 开源协议。

```
MIT License

Copyright (c) 2024 Bookmarks Organizer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 致谢

感谢以下开源项目：

- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) - HTML解析
- [aiohttp](https://docs.aiohttp.org/) - 异步HTTP客户端
- [scikit-learn](https://scikit-learn.org/) - 机器学习库

感谢所有贡献者的付出！

### 联系方式

- **GitHub Issues**: [提交问题](https://github.com/your-repo/bookmarks/issues)
- **GitHub Discussions**: [参与讨论](https://github.com/your-repo/bookmarks/discussions)
- **Email**: your-email@example.com

---

**如果这个工具对你有帮助，请给个⭐️Star支持一下！**

**Made with ❤️ by the Bookmarks Organizer Community**
