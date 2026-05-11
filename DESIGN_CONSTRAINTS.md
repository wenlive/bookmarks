---
name: bookmark-organizer-design-constraints
description: 长期稳定的产品和实现约束。代码、prompt、报告和工作流的行为改动都必须继续遵守这些约束。
---

# Design Constraints

## Purpose

这份文档记录应该跨越重构和运行轮次长期保持稳定的约束。

它不是运行手册，也不是历史记录。
如果某个行为变化违反了这里的规则，应视为设计回退，而不是中性重构。

## 1. 通用化优先，不把当前用户分布写进默认行为

Tracked source 必须服务不同用户，而不是围绕当前一份书签集定制。

必须保持：

- 不把个人主题树、个人 trusted domain、个人偏好目录结构硬编码进默认逻辑
- Tracked default 只承载通用 guardrail、资源类型、质量信号、generic-platform suppression 和 explainable threshold
- 用户特定 specialization 放入 generated files，而不是放入 tracked source
- 能用 reusable signal 解决的问题，优先不用 `if domain == ... then category` 式 one-off 规则

用户级 specialization 的推荐落点：

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

## 2. LLM-Aided，而不是 LLM-Embedded

LLM 可以参与 taxonomy 生成和 follow-up 补全，但本地默认流水线不应绑定某个在线 provider。

必须保持：

- LLM integration 通过文件和 schema 进行
- 本地脚本负责导出 prompt 和 structured evidence
- 外部 LLM 或 agent 负责产出 strict JSON
- 本地导入脚本负责回灌 generated taxonomy / assignments
- 默认路径不引入 direct vendor SDK call

当 agent 负责执行完整工作流时，必须保持：

- 不能只生成 prompt 就停下
- 必须读取 prompt 和其配对的 structured evidence JSON
- 必须产出 strict schema-valid response JSON
- 响应仍要遵守 generic-platform suppression 和其他仓库约束

## 3. 网络与代理行为必须显式

真实抓取环境里，direct 和 proxy 都可能正确，也都可能失败。

必须保持：

- 保留 direct fetch
- 保留 proxy fetch
- 保留 proxy-first plus direct-retry，除非用户明确要求别的策略
- 在结果里保留抓取 provenance 和失败语义
- 当真实抓取明显需要代理时，向操作者清楚暴露代理环境变量和 flag 用法

标准代理示例：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

不要用“放宽 trusted access”去掩盖真实网络不确定性。

## 4. 优先从当前用户语料中提取结构

系统应当学习当前用户书签集中反复出现的产品名、项目名、域名家族和稳定标题短语，而不是只依赖全局常见词。

必须保持：

- 允许从当前语料提取 corpus-level signal
- 允许在 prompt bundle 和报告中暴露这些信号
- 优先把可复用的提取逻辑放进 `signal_pack/v2`
- 不因为“这是小众主题”就强行退回 tracked hardcode 或忽略用户内部强信号

## 5. 输出首先是浏览产品，而不是纯聚类产物

最终 HTML 不是只给指标看的，它是要被人重新导入浏览器继续使用的。

必须保持：

- 顶层结构适合浏览和重找
- 正常主题、`待整理`、`发现主题`、`待审阅` 之间保持明确分离
- 不为了 purity 把正常主题压成少数巨型根目录
- 也不为了细粒度把大量微小主题炸成顶层噪音
- cluster label 优先是人能理解的主题名，而不是平台名、栏目名或站点 slogan

## 6. 通用平台默认是来源信号，不是主题信号

这些 broad platform 只应默认扮演 source signal：

```text
github.com
github.io
gitlab.com
gitee.com
bitbucket.org
stackoverflow.com
stackexchange.com
medium.com
zhihu.com
csdn.net
51cto.com
jianshu.com
docs.qq.com
qq.com
google.com
notion.so
youtube.com
bilibili.com
```

必须保持：

- 不把它们作为 tracked default 的 topic domain
- 不根据这些平台本身创建主题分类
- 允许 title、description、repo name、product name、schema type、path segment、page content 提供主题证据

## 7. 状态持久化与变更纪律必须清晰

必须保持：

- 这份文档持续 tracked
- `README.md`、`SERVICE_CONTRACT.md`、`RUNBOOK.md`、`AGENTS.md` 在行为变化时同步更新
- generated state 和运行产物保持 ignored
- 行为级变更要补测试或报告校验，而不是只改代码

在完成行为改动前，至少检查：

- 是否把当前用户分布写进了 tracked defaults
- 是否引入了默认在线模型依赖
- 是否把 direct/proxy 语义写模糊了
- 是否牺牲浏览层可用性换取表面覆盖率
- 是否让 `folder_only`、`low_confidence_normal`、`generic_platform_domain_suggestion` 或 `fetch_blocked_discovery` 指标回退

## Preferred Patterns

- generic guardrail in tracked code
- user-specific specialization in generated taxonomy / assignments
- prompt bundle + strict JSON import for LLM-assisted refinement
- evidence-driven reports
- explainable classification and clustering behavior

## Avoid

- broad platform domains as topic domains
- folder-only or folder-dominant topic assignment
- hidden online dependencies in the local pipeline
- personal topic defaults committed into shared source
- display structures that make the bookmark bar harder to browse
