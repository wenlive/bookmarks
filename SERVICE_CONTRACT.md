---
name: bookmark-organizer-service-contract
description: 面向外部人类使用者和 agent 的本地服务契约。说明输入、输出、副作用、运行模式、网络行为、LLM 交接点和状态生命周期。
---

# Service Contract

## 定位

把这个仓库当作一个本地 skill/service 时，应该以本文件为契约，而不是从实现细节倒推行为。

它回答的是：

- 输入是什么
- 输出是什么
- 运行会改哪些文件
- 哪些步骤会引入 LLM 能力
- 网络和代理是什么语义
- 清理和复用状态是什么语义

## 服务摘要

输入一份 Chrome 导出的书签 HTML，服务会：

1. 复制到项目工作路径
2. 解析书签与重复 URL
3. 抓取页面与站点级元信息
4. 做保守分类
5. 做聚类和显示层次整理
6. 生成可重新导入 Chrome 的 HTML

默认目标不是最大化“覆盖率”，而是最大化“可解释、可复核、少错分”的可用性。

## 输入契约

### 必需输入

- 书签 HTML：Chrome 导出的 Netscape Bookmark 文件
- 配置文件：通常是 `skill_config.json`

默认路径：

```text
input HTML: data/bookmarks.html
config:     skill_config.json
```

### 可选输入

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`
- `data/bookmarks_with_info.json`

可选输入语义：

- 如果 generated taxonomy / assignments 存在，正常运行会自动消费
- 如果抓取缓存存在，抓取阶段会优先复用已有成功结果，除非显式清理或强制重抓

## 输出契约

### 主输出

- `output/organized_bookmarks.html`

这是可直接导入 Chrome 的目标 HTML。

### 报告输出

- `output/reports/duplicates.json`
- `output/reports/broken_links.json`
- `output/reports/review_queue.json`
- `output/reports/fetch_hotspots.json`
- `output/reports/needs_confirmation.json`
- `output/reports/rule_suggestions.json`
- `output/reports/quality_report.json`
- `output/reports/signal_audit.json`

### LLM prompt bundle 输出

bootstrap：

- `output/reports/taxonomy_bootstrap_prompt.md`
- `output/reports/taxonomy_bootstrap_clusters.json`

follow-up：

- `output/reports/taxonomy_followup_prompt.md`
- `output/reports/taxonomy_followup_candidates.json`

### Generated 状态输出

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

这些文件是用户级 specialization，不应被当成 tracked source。

## 副作用与状态生命周期

运行会写入这些区域：

- `data/`
- `output/`
- `logs/`

关键状态语义：

- `data/bookmarks.html`：项目内复制后的输入
- `data/parsed_bookmarks.json`：解析结果
- `data/bookmarks_with_info.json`：抓取缓存和增强元信息
- `data/classified_bookmarks.json`：分类结果
- `data/clustering_result.json`：聚类与层级结果

### 清理语义

`--clear-fetch-cache`

- 只删除抓取缓存
- 当前代码对应的是 `data/bookmarks_with_info.json`

`--reset-all`

- 删除中间产物、最终输出、报告和日志
- 当前代码会删除：
  - copied HTML
  - parsed JSON
  - fetch cache
  - classified JSON
  - clustering JSON
  - output HTML
  - output/reports
  - logs
- 当前代码 **不会** 删除：
  - `data/generated/user_taxonomy.json`
  - `data/generated/bookmark_taxonomy_assignments.json`

这点必须按当前实现理解，不能把 `--reset-all` 视作“完全清空所有 generated state”。

## 运行模式契约

### 1. 普通运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

语义：

- 复用已有 generated taxonomy / assignments
- 复用已有 fetch cache
- 跑完整六步主链

### 2. 代理优先运行

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

语义：

- 第一轮优先走代理
- 对代理后仍 unresolved 的条目做一次 direct retry
- 最终统计会保留多轮抓取信息，而不是只保留最后一跳

### 3. taxonomy bootstrap

```bash
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy
```

语义：

- 跑到 classify 为止
- 额外生成 bootstrap prompt bundle
- 生成 prompt 后即结束，不继续执行主链后半段

### 4. taxonomy follow-up

```bash
python3 scripts/generate_taxonomy_followup.py --config skill_config.json
```

语义：

- 不是普通主链默认步骤
- 只负责为长尾 `rule_gap` 生成 follow-up 候选包和 prompt

## 网络契约

当前抓取层显式支持三种路径：

- direct
- proxy
- proxy-first then direct-retry

默认要求：

- 不要假设代理永远正确
- 也不要假设直连永远正确
- 抓取结果必须保留 provenance 和失败原因

建议代理环境变量：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

当前抓取层还显式包含这些通用能力：

- per-host throttle
- same-origin warmup retry
- homepage fallback on failure
- default-off external metadata fallback

## LLM 契约

### 当前有几个 LLM 介入点

当前设计里，显式且仅显式存在两个 LLM-assisted 关键步骤：

1. taxonomy bootstrap
2. taxonomy follow-up

没有第三个默认模型步骤，也没有隐藏在线 API 调用。

### 每个步骤必须读什么

bootstrap 必须一起读：

- `output/reports/taxonomy_bootstrap_prompt.md`
- `output/reports/taxonomy_bootstrap_clusters.json`

follow-up 必须一起读：

- `output/reports/taxonomy_followup_prompt.md`
- `output/reports/taxonomy_followup_candidates.json`

### 响应要求

- 响应必须是 strict JSON
- 允许是 fenced `json` 代码块，导入脚本会兼容
- 响应必须通过 `scripts/apply_taxonomy_response.py` 回灌
- agent 如果负责完整工作流，不能只生成 prompt 就停下

### 导入命令

bootstrap：

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
```

follow-up：

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_followup_response.json --clusters output/reports/taxonomy_followup_candidates.json --merge-existing
```

## 行为保证

本服务默认保持这些行为：

- 原始 Chrome 文件夹路径不是强 topic evidence
- broad platform domain 不会作为 topic domain 被默认引入
- 抓取失败不会直接删除书签
- review-required 条目会镜像到 `待审阅`
- 低置信正常分类应该回落到 `待整理`
- generated taxonomy 负责用户特定 specialization，而不是 tracked default source

## 非目标

本服务当前不承诺这些事情：

- 不承诺所有页面都能抓到正文
- 不承诺所有书签都能自动进入稳定主题
- 不承诺默认运行会自动调用任意在线模型
- 不承诺 `--reset-all` 会清除用户级 generated taxonomy
- 不承诺发现主题命名一定完全语义化

## 作为外部 skill/service 消费时的最短路径

### 纯本地无 LLM

```bash
./organize.sh data/bookmarks.html skill_config.json
```

### 带两阶段 LLM 增强

1. 跑 bootstrap
2. 读取 bootstrap prompt + clusters
3. 产出并应用 `taxonomy_response.json`
4. 重跑 downstream
5. 生成 follow-up prompt + candidates
6. 读取 follow-up prompt + candidates
7. 产出并合并 `taxonomy_followup_response.json`
8. 重跑 downstream

这就是当前最完整的“最优能力”服务形态。
