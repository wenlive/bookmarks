# Chrome书签智能整理工具

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-1.1.1-orange)

> 基于 Python 的 Chrome 书签整理流水线，支持配置驱动、日志输出、重复/失效链接报告与待确认分类导出。

## 当前版本重点

v1.1.1 在上一版基础上继续修正了几个明显问题：

- 配置文件中的相对路径现在按**配置文件所在目录**解析，而不是固定按仓库根目录解析。
- `organize.sh` 在输入文件与目标文件相同时不再触发自复制报错。
- 解析步骤增加重复 URL 报告，抓取步骤增加失效链接报告。
- 待确认报告不再混入默认“未分类”项，减少人工复核噪音。

## 快速开始

### 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

### 一键运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

### 默认输出

- 整理后的 HTML：`output/organized_bookmarks.html`
- 重复 URL 报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 日志文件：`logs/bookmarks_organizer.log`

## 分步运行

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 配置说明

核心配置都在 `skill_config.json`，并且支持：

- 输入/输出路径；
- 报告文件路径；
- 抓取并发、超时、重试、延迟；
- 分类权重与确认阈值；
- 聚类阈值；
- 日志级别与日志文件路径。

> 注意：当你传入一个外部配置文件时，配置里的相对路径会相对于**该配置文件所在目录**解释。

## 当前能力

### 已完成

- 去除硬编码绝对路径。
- `organize.sh` 支持输入文件和配置文件。
- 所有步骤接通配置文件与 CLI 参数。
- 新增基础测试。
- 精简依赖。
- 日志输出接入。
- 分类 scoring 与关键词聚类优化。
- 导出重复 URL / 失效链接 / 待确认三类报告。

## 测试

```bash
pytest
python3 -m compileall scripts tests
```
