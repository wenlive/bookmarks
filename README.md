---
name: chrome-bookmark-organizer
description: 中文优先的项目入口文档。说明这个 Chrome 书签整理项目是什么、适合谁、怎么开始，以及应该先看哪些文档。
---

# Chrome Bookmark Organizer

## 它是什么

这是一个本地运行的 Chrome 书签整理流水线。

它接收浏览器导出的书签 HTML，经过：

```text
copy -> parse -> fetch -> classify -> cluster -> generate html
```

输出一个可重新导入 Chrome 的整理后 HTML，以及一组用来解释不确定性、抓取失败和规则缺口的报告。

项目目标不是“尽量少留 `待整理`”，而是：

- 尽量减少错误归类
- 不让旧 Chrome 文件夹主导主题判断
- 不把 GitHub、知乎、CSDN 这类通用平台误当成主题
- 保留抓取失败和不确定项，并明确告诉你为什么需要复核

## 适合谁

- 人类使用者：你有一份 Chrome 导出的书签，希望得到更可浏览的导入结果
- Agent 使用者：你要代替用户跑完整流程，或者继续改进这个项目
- 外部集成者：你想把这个仓库当成一个本地 skill/service 来消费，而不是只把它当源码看

## 最短开始路径

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

普通运行：

```bash
./organize.sh data/bookmarks.html skill_config.json
```

如果抓取需要代理，优先使用：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

最终 HTML 默认输出到：

```text
output/organized_bookmarks.html
```

## 先不要误解这些行为

- 原始 Chrome 文件夹路径只是弱上下文，不是强主题证据
- `github.com`、`zhihu.com`、`csdn.net`、`medium.com` 等通用平台默认不是 topic domain
- 输出 HTML 的书签数可能大于输入，因为 `待审阅` 是镜像层，不是重复 bug
- 正常运行会自动消费已有的 `data/generated/user_taxonomy.json` 和 `data/generated/bookmark_taxonomy_assignments.json`
- 项目默认不会内嵌或自动调用任何在线模型 API

## 你应该先看哪份文档

### 如果你是人

1. `README.md`
2. `SERVICE_CONTRACT.md`
3. `RUNBOOK.md`
4. `QUICK_REFERENCE.md`

### 如果你是 agent

1. `README.md`
2. `SERVICE_CONTRACT.md`
3. `AGENTS.md`
4. `DESIGN_CONSTRAINTS.md`
5. `RUNBOOK.md`

## 文档地图

| 文档 | 用途 |
| --- | --- |
| `SERVICE_CONTRACT.md` | 把本项目当作本地 skill/service 消费时的输入、输出、副作用、LLM 交接点、网络和状态契约 |
| `RUNBOOK.md` | 人类操作者的完整运行手册、命令、报告解释和排障路径 |
| `QUICK_REFERENCE.md` | 极简命令卡，不承担长解释 |
| `AGENTS.md` | agent 执行和改造仓库时必须遵守的操作与实现契约 |
| `DESIGN_CONSTRAINTS.md` | 长期稳定的产品和实现约束，所有行为级改动都要遵守 |

## 当前工程边界

本项目已经处在“信息流优先”的版本：

- 共享信号契约是 `signal_pack/v2`
- 阶段产物带 `schema_version`
- 下游步骤会拒绝消费旧 schema 的 payload
- 分类、聚类和报告都保留决策证据，而不是只产最终树形结果

当前阶段 schema：

- `fetch_output/v2`
- `classified_output/v2`
- `clustering_output/v2`
- `signal_pack/v2`

## 两个 LLM 介入点

当前链路里显式存在且仅存在两个 LLM-assisted 步骤：

1. taxonomy bootstrap
2. taxonomy follow-up

这两个步骤都不是“仓库自动调模型 API”，而是：

- 先由本地脚本导出 prompt 和结构化证据
- 再由外部 LLM 或 agent 产出 strict JSON
- 最后用本地导入脚本回灌 generated taxonomy 和 assignments

如果 agent 负责完整执行，它不能只生成 prompt 就停下，必须继续读取配套 JSON evidence 并产出可导入的 response。

## 关键路径文件

| 路径 | 作用 |
| --- | --- |
| `organize.sh` | 一键运行入口 |
| `skill_config.json` | 运行配置 |
| `scripts/3_fetch_webpage_info.py` | 抓取、代理、缓存、失败归因 |
| `scripts/4_classify_bookmarks.py` | 分类与确认原因生成 |
| `scripts/5_cluster_bookmarks.py` | 聚类、显示层级、规则建议和质量报告 |
| `scripts/apply_taxonomy_response.py` | 导入外部 LLM 产出的 strict JSON |

## 产物概览

主要输出：

- `output/organized_bookmarks.html`

关键报告：

- `output/reports/review_queue.json`
- `output/reports/fetch_hotspots.json`
- `output/reports/needs_confirmation.json`
- `output/reports/rule_suggestions.json`
- `output/reports/quality_report.json`
- `output/reports/signal_audit.json`

Generated 状态：

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

## 下一步看哪里

- 把它当服务消费：看 `SERVICE_CONTRACT.md`
- 实际跑一遍：看 `RUNBOOK.md`
- 只想找命令：看 `QUICK_REFERENCE.md`
- 要让 agent 严格按约束做事：看 `AGENTS.md`
- 要改行为但不想踩设计红线：看 `DESIGN_CONSTRAINTS.md`
