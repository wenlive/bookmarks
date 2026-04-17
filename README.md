# Chrome书签智能整理工具

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-1.2.0-orange)

> 基于 Python 的 Chrome 书签整理流水线，支持配置驱动、代理抓取、增量重试、异常链接待审阅归档与 Chrome 可导入 HTML 导出。

## 当前版本重点

v1.2.0 在上一版基础上新增了几个实用能力：

- 抓取步骤支持显式代理，可选择读取 `https_proxy/http_proxy/all_proxy`。
- 抓取缓存支持**增量复用**，重新执行时默认只重试非成功项，不重复抓取已成功书签。
- 对 `知乎 / CSDN / GitHub / GitBook` 等高频反爬站点加入受信任访问策略，`403/406/429` 及部分抓取异常不再默认进入 `待审阅`。
- 新增统一的待审阅异常报告，并在导出书签中增加顶层 `待审阅` 目录。
- 导出层级增加展示分组，优先保留叶子分类并压平过深目录，减少书签栏最外层文件夹数量。
- 支持清理抓取缓存或删除全部中间产物后重新开始。

## 使用入口

- 总览说明：`README.md`
- 快速命令：`QUICK_REFERENCE.md`
- 日常操作手册：`RUNBOOK.md`

## 快速开始

### 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

如果本机某个 Python/conda 环境存在 SSL 或联网问题，建议切换到能正常访问 HTTPS 的 Python 环境后再运行。

### 一键运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

如果需要显式启用代理并从环境变量读取代理地址：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

如果要删除抓取缓存后重新抓取：

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

如果要删除全部中间产物和输出，再从头构建：

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

### 默认输出

- 整理后的 HTML：`output/organized_bookmarks.html`
- 重复 URL 报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 待审阅异常报告：`output/reports/review_queue.json`
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

如果只想重试抓取失败项并使用代理：

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

如果要忽略已有成功缓存、重新全量抓取：

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --force-refetch
```

## 配置说明

核心配置都在 `skill_config.json`，并且支持：

- 输入/输出路径；
- 报告文件路径；
- 抓取并发、超时、重试、延迟；
- 代理开关与代理地址；
- 受信任站点待审阅策略；
- 是否强制全量重抓；
- 分类权重与确认阈值；
- 聚类阈值、顶层展示分组与目录深度；
- 日志级别与日志文件路径。

> 注意：当你传入一个外部配置文件时，配置里的相对路径会相对于**该配置文件所在目录**解释。

### 抓取相关配置

`fetch_options` 里当前支持：

- `concurrent_limit`
- `timeout`
- `delay`
- `batch_size`
- `max_retries`
- `force_refetch`
- `cache_file`
- `user_agent`
- `review_policy.trusted_access.enabled`
- `review_policy.trusted_access.domain_suffixes`
- `review_policy.trusted_access.http_statuses`
- `review_policy.trusted_access.allow_reason_codes`
- `proxy.enabled`
- `proxy.trust_env`
- `proxy.http_proxy`
- `proxy.https_proxy`
- `proxy.all_proxy`

### 代理行为

- 默认不会自动启用代理。
- 只有配置文件显式开启，或命令行传入 `--use-proxy` 时，抓取步骤才会走代理逻辑。
- 传入 `--trust-env` 后，`aiohttp` 会读取当前 shell 中的 `https_proxy/http_proxy/all_proxy`。

## 当前能力

### 已完成

- 去除硬编码绝对路径。
- `organize.sh` 支持输入文件、配置文件以及抓取步骤透传参数。
- 所有步骤接通配置文件与 CLI 参数。
- 新增基础测试。
- 精简依赖。
- 日志输出接入。
- 分类 scoring 与关键词聚类优化。
- 导出重复 URL / 失效链接 / 待确认 / 待审阅四类报告。
- 抓取缓存增量复用与失败重试。
- 导出书签时保留原分类，同时把异常链接镜像到 `待审阅` 目录。
- 修复聚类伪重复目录问题。

## 导出结果说明

- 最终 HTML 可以直接导入 Chrome。
- 异常链接不会被自动删除。
- 异常链接会同时出现在：
  - 原来的整理分类中
  - 顶层 `待审阅` 目录中
- 因此最终 HTML 中的书签数量可能**大于原始书签数量**，这是预期行为，不是重复导出 bug。

## 最小示例

下面用一个最小示例说明输入和输出的大致形态。

### 输入示意

假设你从 Chrome 导出的书签里有这 4 条：

- `PostgreSQL Docs` -> `https://www.postgresql.org/docs/`
- `TiDB 架构` -> `https://docs.pingcap.com/zh/tidb/stable/tidb-architecture`
- `某篇知乎文章` -> `https://zhuanlan.zhihu.com/p/123456`
- `旧博客链接` -> `https://old.example.com/post/1`

### 输出目录结构示意

项目整理后，导出的 HTML 在 Chrome 中大致会表现为：

```text
书签栏
├── 数据库
│   ├── PostgreSQL
│   │   └── PostgreSQL Docs
│   └── TiDB
│       └── TiDB 架构
├── 技术博客
│   └── 知乎
│       └── 某篇知乎文章
└── 待审阅
    └── DNS/连接失败
        └── 旧博客链接
```

### 说明

- `PostgreSQL Docs` 和 `TiDB 架构` 会进入正常分类。
- `某篇知乎文章` 如果抓取时返回常见反爬响应（如 `403`），会保留在原分类中，默认不进入 `待审阅`。
- `旧博客链接` 如果域名解析失败或站点已不可达，会进入 `待审阅` 对应异常目录。
- 因为异常链接会在两个地方同时出现，所以导出的总书签数可能比原始书签数多。

## 常见用法

### 1. 初次全量整理

```bash
./organize.sh data/bookmarks.html skill_config.json
```

### 2. 忘记开代理后补跑

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

此时步骤 3 会复用已有成功抓取结果，只重试失败项，不需要清空历史缓存。

### 3. 强制重新抓取全部网页

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

### 4. 清理抓取缓存后重新开始

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

### 5. 删除全部中间产物和输出

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

## 测试

```bash
pytest
python3 -m compileall scripts tests
```
