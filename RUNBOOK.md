---
name: bookmark-organizer-runbook
description: 人类操作者的运行手册。说明如何执行主流程、何时重跑哪些阶段、怎么看报告，以及如何排查常见问题。
---

# Runbook

## 使用原则

这份文档只回答“怎么跑”和“怎么判断结果”。

- 服务输入输出契约：看 `SERVICE_CONTRACT.md`
- Agent 约束：看 `AGENTS.md`
- 长期设计边界：看 `DESIGN_CONSTRAINTS.md`

## 默认路径

```text
input:   data/bookmarks.html
config:  skill_config.json
output:  output/organized_bookmarks.html
log:     logs/bookmarks_organizer.log
```

## 预检查

```bash
python3 --version
python3 -c "import bs4, aiohttp"
```

如需确认网络连通性，可做一次轻量直连测试：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)"
```

如果依赖未装：

```bash
python3 -m pip install -r requirements.txt
```

## 标准运行命令

### 普通运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

### 代理优先运行

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

这三个 flag 的语义是：

- `--use-proxy`：显式启用代理模式
- `--trust-env`：从 shell 环境变量读取代理
- `--direct-retry-after-proxy`：代理首轮后，仅对仍 unresolved 的条目做一次 direct retry

### 全量忽略成功缓存重抓

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

如需配合代理：

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch --use-proxy --trust-env --direct-retry-after-proxy
```

### 快速建立网络基线

对大型书签集首次试跑时，可以降低单页等待和重试次数：

```bash
./organize.sh data/bookmarks.html skill_config.json --timeout 6 --max-retries 0 --delay 0
```

该模式适合尽快获得第一份分类与聚类基线，不应被当成最终链接健康结论。抓取阶段会在每个 batch 后原子写入 checkpoint；中断后重跑相同命令会复用已成功结果，并继续重试 unresolved 条目。

## 清理与状态复用

### 只清抓取缓存

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

当前代码对应删除：

```text
data/bookmarks_with_info.json
```

### 清中间产物、输出和日志

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

当前代码会清：

- `data/bookmarks.html`
- `data/parsed_bookmarks.json`
- `data/bookmarks_with_info.json`
- `data/classified_bookmarks.json`
- `data/clustering_result.json`
- `output/organized_bookmarks.html`
- `output/reports/`
- `logs/bookmarks_organizer.log`

当前代码不会清：

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

如果你要“保留抓取缓存但清其他中间结果”，需要手动控制，不能直接把 `--reset-all` 当成那个语义。

## 分步执行

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 局部重跑

### taxonomy 或分类逻辑变了

```bash
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

### 聚类或显示逻辑变了

```bash
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

### 只改了 HTML 渲染

```bash
python3 scripts/6_generate_html.py --config skill_config.json
```

## taxonomy bootstrap

### 生成 prompt bundle

```bash
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy
```

这一步会：

- 跑到 classify 为止
- 生成 bootstrap prompt bundle
- 直接结束，不继续执行 cluster/html

产物：

```text
output/reports/taxonomy_bootstrap_prompt.md
output/reports/taxonomy_bootstrap_clusters.json
```

### 应用外部 LLM 响应

把响应保存为：

```text
data/generated/taxonomy_response.json
```

然后执行：

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

### 重要约束

- 响应必须匹配 strict JSON schema
- `title_patterns` 默认按 literal phrase 处理
- 确实需要正则时，才使用 `{ "regex": "..." }`
- 不要把 GitHub、知乎、CSDN、Medium、Stack Overflow 之类 broad platform 写成 topic domain

## taxonomy follow-up

当 bootstrap 后仍有大量 `rule_gap` 时，再做 follow-up。

### 生成 follow-up 候选包

```bash
python3 scripts/generate_taxonomy_followup.py --config skill_config.json
```

产物：

```text
output/reports/taxonomy_followup_prompt.md
output/reports/taxonomy_followup_candidates.json
```

### 合并 follow-up 响应

把响应保存为：

```text
data/generated/taxonomy_followup_response.json
```

执行：

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_followup_response.json --clusters output/reports/taxonomy_followup_candidates.json --merge-existing
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 最应该看的报告

### `duplicates.json`

- 看原始 URL 去重情况
- 用来解释为什么解析后书签数可能少于输入 HTML 中的 `<A>` 数

### `review_queue.json`

- 看所有内部 review-required 的内容补充异常
- 它用于网络诊断，不再等同于 HTML 中的 `待审阅`
- HTML 只镜像 `user_action_required`：疑似失效、无效地址、证书异常等用户可处理问题

### `fetch_hotspots.json`

- 看失败热点域名和原因分布
- 如果用了代理直连双通路，这里会体现多轮抓取的净变化

### `needs_confirmation.json`

- 看哪些条目仍然不能稳定归入正常主题
- 重点关注三个 bucket：
  - `rule_gap`
  - `fetch_blocked`
  - `low_confidence`

### `rule_suggestions.json`

- 这是基于本次输入动态生成的规则建议
- 它不是固定写死的默认规则文件
- 用来辅助判断是否值得补 generated taxonomy

### `quality_report.json`

- 看整体质量护栏是否破坏
- 最重要的守门指标：
  - `folder_only_classification_count == 0`
  - `low_confidence_normal_category_count == 0`
  - `generic_platform_domain_suggestion_count == 0`
  - `fetch_blocked_discovery_cluster_count == 0`
  - `display_missing_bookmark_count == 0`
  - `display_duplicate_bookmark_count == 0`
- 看无正文降级质量：`content_unavailable_normal_category_share`
- 看目录可浏览性：`display_max_depth`、`display_max_leaf_bookmarks`

### `signal_audit.json`

- 看抓到了哪些信号、真正用上了哪些信号
- 适合在“要加新 signal 还是加新规则”之间做判断

## 如何理解结果层级

- `待审阅`：链接本身可能需要用户处理；普通抓取失败不会出现在这里
- `待整理`：证据不足，不应该冒险塞进正常主题
- `发现主题`：有一定聚合意义，但当前 taxonomy 还不支持稳定落位

输出 HTML 中书签数大于输入，并不自动表示有重复，常见原因是：

- `待审阅` 镜像

## 常见排障

### 内容补充失败过多

先区分问题来源：

- transport 问题：代理、DNS、timeout、证书
- 站点策略问题：`access_denied`、疑似反爬
- 页面真实失效：`not_found`

建议路径：

1. 先看 `review_queue.json`
2. 再看 `fetch_hotspots.json`
3. 如需真实抓取，优先尝试代理优先模式
4. 分类仍可依赖保存标题、URL 和 generated taxonomy；不要为了覆盖率放宽正常分类阈值
5. 用 `content_unavailable_outcome` 判断无正文时的实际产出，不要用 HTML 中的 `待审阅` 数量推断抓取质量

### `待整理` 太多

优先判断是哪一种：

- `rule_gap`：说明需要 generated taxonomy 或 follow-up
- `fetch_blocked`：内部表示可用内容信号不足；最终 HTML 显示为更中性的“信息不足”
- `low_confidence`：说明现有证据不足以稳定归类

先看：

- `needs_confirmation.json`
- `rule_suggestions.json`
- `signal_audit.json`

### `发现主题` 命名噪声太大

这通常意味着：

- taxonomy 覆盖还不够
- 聚类命名仍受 source-like token 干扰

不要直接把这些名字硬编码进 tracked default source，优先判断它们是否值得进入 generated taxonomy。

### 正常结果被历史 generated taxonomy 污染

检查这两个文件是否还存在：

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

它们存在时，普通运行会自动消费。
如果你要做纯基线重跑，需要单独处理它们。

### LLM 响应导入失败

先检查：

- 是否包含合法 JSON
- `schema_version` 是否正确
- `cluster_id` 是否来自对应的 prompt bundle
- 是否把 broad platform 当成 topic domain

可直接查看：

```bash
python3 scripts/apply_taxonomy_response.py --help
```

## 提交前检查

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/generate_taxonomy_followup.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```
