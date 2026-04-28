# Runtime Follow-up TODO

## 目的

这份文档记录 `2026-04-28` 针对真实书签导出 `data/bookmarks_2026_4_28.html` 的完整验证、修复和剩余问题，供下次继续优化时直接接续，不需要重新还原上下文。

注意：本文中出现的具体分类名、数量和旧规则建议是无 taxonomy 重构前的历史基线，用于理解问题来源，不代表当前默认配置。当前默认配置不再内置 `category_rules` 文件，也不再预设技术主题；新的确定性约束应来自 taxonomy bootstrap 生成的 `data/generated/user_taxonomy.json` 和 `data/generated/bookmark_taxonomy_assignments.json`。

## 本轮背景

- 用户要求用桌面上的最新浏览器书签导出，清理历史残留，完整跑通 `copy -> parse -> fetch -> classify -> cluster -> generate html`。
- 本轮之前，代码已经完成信息流治理改造：
  - `signal_pack/v2`
  - stage `schema_version` 校验
  - `signal_audit.json`
  - 分类/聚类证据追踪
- 本轮的重点不是新增功能，而是用真实输入确认流程是否真的可用，并把真实运行暴露的问题修掉。

## 输入与运行命令

### 输入文件

- 原始导出：`/Users/lipoqi/Desktop/bookmarks_2026_4_28.html`
- 已复制到仓库：`data/bookmarks_2026_4_28.html`

### 真实可复现命令

全量运行：

```bash
env https_proxy=http://127.0.0.1:7897 \
    http_proxy=http://127.0.0.1:7897 \
    all_proxy=socks5://127.0.0.1:7897 \
    conda run -n base ./organize.sh data/bookmarks_2026_4_28.html skill_config.json --reset-all --use-proxy --trust-env --direct-retry-after-proxy
```

仅重跑下游：

```bash
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 本轮真实运行过程

### 第一次尝试

- 历史记录：在沙箱内直接跑旧基线导出时，抓取曾出现 `1012/1012` 全失败。
- 失败原因不是分类/聚类逻辑崩溃，而是沙箱内网络/DNS 不可用。
- 证据：
  - `Cannot connect to host ... [nodename nor servname provided, or not known]`
  - 几乎所有失败都被归到 `dns_connection`

结论：

- 不能用沙箱内的抓取结果评估真实质量。
- 必须用外部网络 + 代理重跑。

### 第二次尝试

用代理在沙箱外全量重跑后，流程跑通，但暴露两个真实问题：

1. 抓取阶段有大量 Brotli 解码失败：
   - `400, message: Can not decode content-encoding: br`
   - 数量：`133`
2. 双通路抓取完成后，最终 `stats.proxy_enabled` 被第二轮直连覆盖成 `false`，会误导后续判断。

### 本轮已修复

#### 抓取层修复

文件：`scripts/3_fetch_webpage_info.py`

- 请求头显式改成 `Accept-Encoding: gzip, deflate`，避免宣告 `br` 后客户端解码失败。
- 根据 `content-type` 在 XML/HTML 之间切换解析器，避免把 XML 一律按 HTML 解析。
- 双通路抓取结束后，保留第一轮代理模式的顶层统计：
  - `proxy_enabled`
  - `proxy_trust_env`

修复后，`br` 失败从 `133` 降到 `0`。

#### 分类层修复

文件：`scripts/4_classify_bookmarks.py`

- 从动态主题候选入口移除了：
  - `structure.homepage_fetch_status`
  - `structure.homepage_source`
- 目的：避免 `success` / `fetched` 这类操作态词被当作主题或聚类线索。

这解决了第一轮真实结果中 `Fetched` 进入发现主题/混合簇命名的问题。

#### 测试补充

文件：`tests/test_pipeline.py`

- 增加 XML 响应解析测试。
- 增加双通路代理统计保留测试。
- 增加分类器忽略 `success` / `fetched` 操作态词的测试。

当前测试状态：

- `pytest -q` -> `59 passed`
- `git diff --check` -> passed

## 当前最新有效结果

以下结果以去除内置 category rule 后、对同一份
`data/bookmarks_2026_4_28.html` 的最终产物为准。当前确定性主题约束来自
忽略的生成文件：

```text
data/generated/user_taxonomy.json
data/generated/bookmark_taxonomy_assignments.json
```

### 解析阶段

- 输入书签数：`1014`
- 重复 URL 组：`92`
- 唯一域名：`439`

### 抓取阶段

- `success_count`: `745`
- `fail_count`: `269`
- `review_free_count`: `745`
- `review_queue`: `269`
- `trusted_override_count`: `0`
- `reused_count`: `745`
- `retried_count`: `269`
- `success_rate`: `73.5%`

说明：

- 当前默认配置不再内置 trusted-access 站点放行规则。
- 待审阅数量上升，是因为旧的站点级 review suppression 被移除；这比把个人偏好硬编码进默认配置更符合通用化目标。
- 抓取剩余问题仍应按热点域名单独处理，不应全局放宽 trusted policy。

### Taxonomy 阶段

- 外部 LLM taxonomy 响应已应用。
- 生成用户分类：`41`
- 生成簇级 assignment：`371`
- `title_patterns` 默认按字面短语处理；只有 `{"regex": "..."}` 对象会被当作正则，避免 `C++`、`TLA+`、`Spider 2.0`、`Go`、`PG` 这类词触发错误正则语义。

### 分类阶段

- `confirm_needed_count`: `470`
- `待整理`: `407`
- `confirmation_bucket_counts.fetch_blocked`: `269`
- `confirmation_bucket_counts.rule_gap`: `202`

主要 root 分布：

- `数据库内核与系统`: `347`
- `编程语言与工程`: `122`
- `存储与新硬件`: `44`
- `工具与个人资源`: `29`
- `分布式系统与一致性`: `24`
- `云服务与平台`: `16`
- `性能测试与可观测性`: `13`
- `大数据与分析生态`: `12`

关键护栏：

- `folder_only_classification_count = 0`
- `low_confidence_normal_category_count = 0`

这说明“旧 Chrome 文件夹不再主导分类”和“低置信正常分类会回落到待整理”这两个关键设计目标仍然成立。

### 聚类阶段

- 顶层根：
  - `主要主题`: `622`
  - `待整理`: `390`
  - `发现主题`: `2`
- `cluster_count`: `623`
- `discovery_cluster_count`: `1`
- `tidy_cluster_count`: `350`
- `mixed_cluster_count`: `5`
- `generic_platform_cluster_count`: `235`
- `largest_generic_platform_cluster_size`: `11`
- `normal_root_direct_bookmark_share`: `0.0`
- `fetch_blocked_discovery_cluster_count`: `0`
- `rule_suggestions_count`: `11`

当前聚类层可以确认的好消息：

- 抓取受阻簇没有再被路由进 `发现主题`。
- generic platform 仍然没有直接被当成正常主题根。
- 旧的发现主题噪声显著下降，混合簇数量也明显下降。

### HTML 阶段

- 输出文件：`output/organized_bookmarks.html`
- 已复制到桌面：`/Users/lipoqi/Desktop/organized_bookmarks_2026_4_28.html`
- `待审阅` 镜像数：`269`

说明：

- 生成 HTML 中的书签数大于输入书签数是预期行为，不是重复 bug。
- 差值来自 `待审阅` 镜像输出。

## 现状评估

## 结论

流程已经进入“默认无内置分类规则，也能通过 bootstrap + 外部 LLM 约束完成真实书签整理”的状态。

当前总体判断：

- `抓取`: 可用，但默认不再用个人 trusted-access 白名单压低待审阅数
- `taxonomy`: 可用，当前生成了 41 个用户主题和 371 个簇级约束
- `分类`: 可用，但未覆盖主题仍会保守进入 `待整理`
- `聚类`: 比旧基线明显更干净，`发现主题` 和 mixed cluster 已大幅下降
- `报告`: 已能支持下一轮 taxonomy bootstrap 和局部抓取专项

## 仍然存在的主要问题

### P0: taxonomy 覆盖仍不完整

当前：

- `待整理`: `407`
- `confirmation_bucket_counts.rule_gap`: `202`
- `confirmation_bucket_counts.fetch_blocked`: `269`

问题本质：

- 默认配置已去掉硬编码主题，这是正确方向；
- 代价是外部 LLM taxonomy 的覆盖率决定了正常分类覆盖率；
- 当前仍有一批成功抓取但没有被 taxonomy 覆盖的主题，例如 Docker/Kubernetes/Greenplum/OpenACID/vLLM 等。

建议方向：

- 继续改进 `taxonomy_bootstrap_prompt.md` 的信息密度和返回约束，让外部模型更容易补齐长尾主题。
- 使用 `taxonomy_bootstrap_clusters.json` 和 `needs_confirmation.json` 选择高证据簇补充 generated taxonomy。
- 个人主题只进入 `data/generated/user_taxonomy.json` 或 `data/generated/bookmark_taxonomy_assignments.json`，不再回写仓库默认规则。

### P0: review queue 变大，需要区分“真实坏链”和“默认不信任”

当前：

- `review_queue`: `269`
- `trusted_override_count`: `0`

问题本质：

- 旧配置通过个人站点 trusted-access 策略压低了待审阅数；
- 通用化后默认不再替任何用户假设 `zhihu`、`csdn`、`github` 等平台“可忽略审阅”；
- 因此待审阅数量上升是预期结果，但需要更好的报告帮助用户决定是否生成个人 trusted policy。

建议方向：

- 在报告中区分 HTTP 阻断、DNS/连接失败、证书失败、超时、站点反爬。
- 可以新增一个“可选 trusted-access 建议”报告，但不要默认启用。
- 对热点域名单独验证 UA、header、timeout、retry 或只抓主页策略。

### P1: generic-platform 簇仍偏多，但规模可控

当前：

- `generic_platform_cluster_count = 235`
- `largest_generic_platform_cluster_size = 11`

问题本质：

- broad platform 域名没有成为主题域名，护栏是好的；
- 但 GitHub、知乎、CSDN、Medium 等来源仍占大量书签，簇标签和 rule suggestions 仍需要继续压制 source-like token。

建议方向：

- 保留 generic platform guardrail，因为这是通用质量保护，不是个人 taxonomy。
- 继续优先使用高置信 taxonomy leaf、稳定项目名、窄域名和多书签共同 token 命名。
- 避免把 broad source platform 写入 generated taxonomy 的 `topic_domains`。

### P1: mixed cluster 降到 5 后，应转为局部回归修复

当前：

- `mixed_cluster_count = 5`

问题本质：

- 大规模跨主题合并已经缓解；
- 剩余问题更可能是个别相似度权重、平台来源、或 taxonomy assignment 不够细。

建议方向：

- 逐个看 `quality_report.json` 的 `largest_mixed_clusters`。
- 优先补簇级 assignment 或调整相似度冲突惩罚，不做全局激进拆分。

### P2: bootstrap 输出和应用流程还可以更稳

当前已修复：

- 外部 `title_patterns` 默认按字面量处理，避免误把普通短语当正则。
- `apply_taxonomy_response.py` 会过滤明显无效或公共后缀类 domain suggestion。

后续建议：

- 给 `taxonomy_bootstrap_prompt.md` 增加更明确的“不要过拟合个人旧目录名”和“不要给 broad platform 加 topic domain”检查清单。
- 在 `apply_taxonomy_response.py` 中继续扩展 schema 校验和可解释 warning。
- 为外部响应中的重复 category、空 path、跨 root assignment 增加回归测试。

## 下次推荐优先级

### 优先级 1

- 提升 taxonomy bootstrap 覆盖率
- 目标：
  - 降低 successful fetch 下的 `rule_gap`
  - 让外部 LLM 更稳定返回可解析、不过拟合的 taxonomy
  - 让 `待整理` 中的高证据主题进入用户生成 taxonomy

### 优先级 2

- 继续清理 generic-platform 命名和局部 mixed cluster
- 目标：
  - 保持 `discovery_cluster_count` 低位
  - 保持 `mixed_cluster_count` 不反弹
  - 让 `rule_suggestions.json` 更偏向真实主题而不是平台/作者/owner

### 优先级 3

- 针对抓取热点域名做专项策略验证
- 重点是生成可解释的个人 trusted-access 建议，而不是默认启用站点白名单

### 优先级 4

- 让 `signal_audit.json` 里的“已采未用”字段开始产生可控收益
- 先从：
  - `status_code`
  - `code_languages`
  - `nav_text`
- 开始，避免一次动太多维度。

## 下次具体执行建议

### 路线 A: 先做 taxonomy bootstrap 改进

适合当前状态，收益最快。

步骤：

1. 改 `scripts/generate_taxonomy_bootstrap.py` 的 prompt 和 cluster 摘要字段。
2. 增补对 broad platform domain、空 category、非法 regex、重复 path 的响应校验测试。
3. 重新生成 `taxonomy_bootstrap_prompt.md`。
4. 用外部 LLM 生成新响应，并通过 `scripts/apply_taxonomy_response.py` 应用。
5. 只重跑 `4/5/6`。
6. 对比：
   - `needs_confirmation.json`
   - `quality_report.json`
   - `rule_suggestions.json`

成功标准：

- successful fetch 下的 `rule_gap` 下降
- `待整理` 下降
- 不引入 broad platform topic domain
- 不引入新的正则误判

### 路线 B: 再做聚类命名清理

步骤：

1. 改 `scripts/5_cluster_bookmarks.py` 的 representative token / cluster label 过滤。
2. 增补作者名、平台 slogan、过泛词的噪声抑制测试。
3. 只重跑 `5/6` 或必要时重跑 `4/5/6`。
4. 对比：
   - `quality_report.json`
   - `rule_suggestions.json`
   - `signal_audit.json`

成功标准：

- `发现主题` 保持低噪声
- generic-platform 最大簇不异常变大
- `create_topic` 建议更像真实主题

### 路线 C: 再做 generated taxonomy 补齐

步骤：

1. 根据 `needs_confirmation.json`、`rule_suggestions.json` 和 `taxonomy_bootstrap_clusters.json` 选高证据候选。
2. 通过 taxonomy bootstrap 响应生成用户 taxonomy，不修改内置规则文件。
3. 增补回归测试。
4. 重跑 `4/5/6`。

成功标准：

- `rule_gap` 下降
- `待整理` 下降
- 不引入新的 `mixed_cluster_count` 上升

### 路线 D: 最后做抓取热点专项

步骤：

1. 抽 `review_queue.json` 里高频失败域名样本。
2. 找每类失败的主模式：
   - 403
   - 521
   - timeout
   - certificate
   - DNS / connect
3. 对单个域名做最小改动验证，不要一次全局放开。
4. 只重跑 `3/4/5/6`。
5. 如果要 suppression，优先生成用户可审核建议，不默认写入 `skill_config.json`。

成功标准：

- `待审阅异常` 继续下降，或者报告能解释为什么保留
- `trusted_override` 不在默认配置中膨胀
- `fetch_blocked` 簇继续收缩

## 下次开始前先看的文件

按顺序：

1. `TODO_RUNTIME_FOLLOWUP.md`
2. `EVIDENCE_DRIVEN_ITERATION.md`
3. `output/reports/quality_report.json`
4. `output/reports/rule_suggestions.json`
5. `output/reports/signal_audit.json`
6. `output/reports/review_queue.json`
7. `scripts/3_fetch_webpage_info.py`
8. `scripts/4_classify_bookmarks.py`
9. `scripts/5_cluster_bookmarks.py`
10. `tests/test_pipeline.py`

## 当前产物位置

- 抓取结果：`data/bookmarks_with_info.json`
- 分类结果：`data/classified_bookmarks.json`
- 聚类结果：`data/clustering_result.json`
- 生成 taxonomy：`data/generated/user_taxonomy.json`
- 生成 assignment：`data/generated/bookmark_taxonomy_assignments.json`
- HTML 输出：`output/organized_bookmarks.html`
- 桌面 HTML：`/Users/lipoqi/Desktop/organized_bookmarks_2026_4_28.html`
- 重复报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 待审阅报告：`output/reports/review_queue.json`
- 质量报告：`output/reports/quality_report.json`
- 规则建议：`output/reports/rule_suggestions.json`
- 信号审计：`output/reports/signal_audit.json`

## 一句话状态总结

项目已经从“能对真实书签稳定工作”升级到“默认无内置主题规则，依靠 generated taxonomy 完成真实整理”；当前最大短板是 taxonomy 覆盖率、可解释的个人 trusted-access 建议、generic-platform 局部命名质量和少量热点域名抓取策略。
