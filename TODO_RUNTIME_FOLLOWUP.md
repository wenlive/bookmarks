# Runtime Follow-up TODO

## 目的

这份文档记录 `2026-04-28` 针对真实书签导出 `data/bookmarks_2026_4_28.html` 的完整验证、修复和剩余问题，供下次继续优化时直接接续，不需要重新还原上下文。

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

- 在沙箱内直接跑时，抓取 `1012/1012` 全失败。
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

以下结果以修复后、对同一份 `data/bookmarks_2026_4_28.html` 的最终产物为准。

### 解析阶段

- 输入书签数：`1012`
- 重复 URL：`199`
- 唯一域名：`437`

### 抓取阶段

- `success_count`: `745`
- `broken_count`: `222`
- `fail_count`: `45`
- `review_free_count`: `920`
- `trusted_override_count`: `175`
- `success_rate`: `73.6%`
- 第二轮直连重试仅处理：`104`
- 双通路增量：
  - `success_delta`: `12`
  - `review_free_delta`: `12`

抓取后剩余待审阅问题分布：

- `http_error`: `47`
- `dns_connection`: `18`
- `timeout`: `15`
- `certificate`: `8`
- `other_error`: `3`
- `invalid_url`: `1`

重点待审阅域名：

- `stackoverflow.com`: `7`
- `book.tidb.io`: `6`
- `www.rfc-editor.org`: `5`
- `medium.com`: `5`
- `pingcap.com`: `4`
- `docs.pingcap.com`: `3`
- `dl.acm.org`: `3`

说明：

- 当前抓取已经从“流程不可用”提升到“流程可用但仍有少量热点域名待专项处理”。
- `待审阅` 数从第一次真实外网跑的 `221` 降到 `92`。

### 分类阶段

- 主题数：`44`
- 资源类型数：`9`
- `confirm_needed_count`: `276`
- `待整理`: `279`
- `rule_gap`: `131`
- `fetch_blocked`: `92`
- `low_confidence`: `56`

主要分类分布：

- `数据库/PostgreSQL`: `72`
- `分布式系统/其他`: `43`
- `Linux系统`: `43`
- `数据库/其他`: `40`
- `机器学习/AI`: `36`
- `阅读资料`: `36`
- `数据库/openGauss`: `35`
- `数据库/TiDB`: `34`
- `编程语言/C-C++`: `33`
- `编程语言/Go`: `30`
- `待整理`: `279`

当前分类层可以确认的好消息：

- `folder_only_classification_count = 0`
- `low_confidence_normal_category_count = 0`

这说明“旧 Chrome 文件夹不再主导分类”和“低置信正常分类会回落到待整理”这两个关键设计目标仍然成立。

### 聚类阶段

- 顶层根：
  - `技术主题`: `522`
  - `工具与平台`: `52`
  - `学习与资料`: `67`
  - `个人与生活`: `22`
  - `待整理`: `277`
  - `发现主题`: `72`
- `cluster_count`: `716`
- `discovery_cluster_count`: `28`
- `tidy_cluster_count`: `242`
- `mixed_cluster_count`: `30`
- `generic_platform_cluster_count`: `299`
- `normal_root_direct_bookmark_share`: `0.1931`
- `fetch_blocked_discovery_cluster_count`: `0`

当前聚类层可以确认的好消息：

- 抓取受阻簇没有再被路由进 `发现主题`。
- generic platform 仍然没有直接被当成正常主题根。

### HTML 阶段

- 输出文件：`output/organized_bookmarks.html`
- HTML 中总书签数：`1104`
- 输入书签数：`1012`

说明：

- `1104 > 1012` 是预期行为，不是重复 bug。
- 差值来自 `待审阅` 镜像输出，当前镜像数约为 `92`。

## 现状评估

## 结论

流程已经从“只能跑测试”进入“可对真实书签稳定运行”的状态。

当前总体判断：

- `抓取`: 可用，仍有少数热点域名待专项优化
- `分类`: 基本可用，规则缺口仍然较多
- `聚类`: 可用，但命名噪声和 generic-platform 混簇仍偏多
- `报告`: 有足够证据驱动下一轮，但建议继续让建议更干净

## 仍然存在的主要问题

### P0: 发现主题命名仍有噪声

当前 `发现主题` 中仍有明显不够“可落规则化”的候选：

- `创作你的创作`
- `Brendangregg`
- `Gitcode`
- `Huaweicloud`
- `IETF`
- `MICROSOFT`
- `NVIDIA`
- `Week`

问题本质：

- 某些 cluster label 仍然更像文章来源、作者名、平台名、站点品牌或页面局部文本，而不是“值得新增分类”的主题。
- 这会污染 `发现主题`，让后续人工补规则效率下降。

建议方向：

- 继续收紧 discovery naming：
  - 强化作者名/品牌名/source-like token 过滤
  - 对 generic-platform cluster 限制用站点 slogan 或品牌词命名
  - 对 `Week`、`Phone`、`Efficient` 这类过泛 token 增加抑制
- 重点文件：
  - `scripts/common.py`
  - `scripts/4_classify_bookmarks.py`
  - `scripts/5_cluster_bookmarks.py`

### P0: generic-platform cluster 仍然偏多

当前：

- `generic_platform_cluster_count = 299`

代表性簇：

- `Alibaba` -> 实际更像 `Tair / AI / 分布式存储` 混合
- `Atanunq` -> 实际更像 `终端工具 / GitHub 仓库集合`
- `Compaction` -> 实际更像 `RocksDB` 主题别名
- `Annotated` -> 实际更像 `Redis` 主题别名
- `Amend` -> 实际更像 `Git` 主题别名

问题本质：

- generic platform 本身没有成为分类根，这是对的；
- 但其簇命名仍然容易落在作者名、仓库名或单篇文章词上，导致 rule suggestion 的质量不稳定。

建议方向：

- 在 generic platform 簇上优先使用：
  - 已有高置信分类叶子
  - cluster 内稳定 topic alias
  - narrow domain/topic 证据
- 降低以下命名优先级：
  - 仓库 owner 名
  - 平台 slogan
  - 单篇标题中偶然出现的短词

### P1: mixed cluster 仍然有 30 个

代表性问题簇：

- `乐知乐享`
- `创作你的创作`
- `技术发表平台`
- `Apache`
- `Facebook`

问题本质：

- 有些簇虽然有共同来源或共同平台，但内部实际跨了多个技术主题。
- 当前拆分规则仍不够强，尤其在成功抓取后 topic evidence 更丰富时，应该更积极按高置信类别拆分。

建议方向：

- 对成功抓取且 `rule_confidence` 高的 bookmark，提升强分类拆簇优先级。
- 对 generic platform 高占比簇，增加“同平台不同主题”的拆分惩罚。
- 重点文件：
  - `scripts/5_cluster_bookmarks.py`

### P1: 规则缺口仍然较多

当前：

- `rule_gap = 131`

热点域名：

- `docs.qq.com`: `11`
- `www.cnblogs.com`: `5`
- `docs.oracle.com`: `4`
- `www.modb.pro`: `3`
- `www.jianshu.com`: `3`
- `blog.csdn.net`: `3`
- `mp.weixin.qq.com`: `2`
- `datatracker.ietf.org`: `2`
- `www.patenthub.cn`: `2`
- `martinfowler.com`: `2`
- `concurrencyfreaks.blogspot.com`: `2`
- `www.vldb.org`: `2`
- `tech.meituan.com`: `2`

问题本质：

- 这些并不一定都该加 domain 规则；
- 很多更适合加窄 alias、title pattern、topic keyword，而不是粗暴加平台域名。

建议方向：

- 优先处理：
  - `martinfowler.com`
  - `datatracker.ietf.org`
  - `concurrencyfreaks.blogspot.com`
  - `tech.meituan.com`
  - `www.vldb.org`
- 对 `docs.qq.com` / `cnblogs` / `csdn` / `jianshu` 这类平台，不加 topic domain，优先做 alias / pattern / token 抑制。
- 重点文件：
  - `data/category_rules.json`
  - `data/category_rules_overrides.json`
  - `tests/test_pipeline.py`

### P1: 抓取剩余热点域名需要专项策略

当前待审阅热点主要集中在：

- `stackoverflow.com`
- `book.tidb.io`
- `www.rfc-editor.org`
- `medium.com`
- `pingcap.com`
- `docs.pingcap.com`
- `dl.acm.org`

问题本质：

- 这些不是“全局抓取失败”，而是“少量域名有自己的失败模式”：
  - `403/521`
  - 直连 DNS/连接失败
  - timeout
  - 证书异常

建议方向：

- 对这些域名做 targeted fetch policy：
  - 单独 UA / header 策略
  - timeout / retry 调整
  - 是否可加入 trusted_access domain rules
  - 是否需要只抓主页而跳过深页
- 注意：
  - 不要为了减少 `待审阅` 而扩大 trusted_access 范围。
  - 先看是否真的是“访问受阻但内容可信”，再决定是否放行。

### P2: 采集信号仍有高价值字段未消费

`signal_audit.json` 当前未充分使用的高价值字段：

- `health_access.status_code`
- `structure.homepage_fetch_status`
- `structure.homepage_source`
- `content.description_candidates`
- `structure.nav_text`
- `structure.generator`
- `content.code_languages`

问题本质：

- 这些信号已经采到了，但还没形成稳定、保守、可解释的下游收益。

建议方向：

- `status_code`
  - 可用于更细的 review / trusted_access 策略
- `homepage_fetch_status` / `homepage_source`
  - 不要进入 topic extraction
  - 可用于质量报告、可信度降权、抓取策略报告
- `nav_text`
  - 可作为 cluster naming 的末级 fallback，但必须先过 source-like 过滤
- `generator`
  - 对 docs/tool/blog 判别可能有用，但只应作为弱证据
- `code_languages`
  - 可辅助编程语言、仓库、文档类资源识别

## 下次推荐优先级

### 优先级 1

- 继续清理 discovery / generic-platform 命名噪声
- 目标：
  - 降低 `discovery_cluster_count`
  - 让 `发现主题` 更接近真正可建规则的新主题
  - 减少 `create_topic` 中的假候选

### 优先级 2

- 用 `quality_report.json` + `rule_suggestions.json` 补一批窄规则
- 优先关注：
  - `martinfowler.com`
  - `datatracker.ietf.org`
  - `concurrencyfreaks.blogspot.com`
  - `tech.meituan.com`
  - `www.vldb.org`
- 同时回看：
  - `Alibaba`
  - `Compaction`
  - `Annotated`
  - `Amend`
  - `Linux`

### 优先级 3

- 针对抓取热点域名做专项策略验证
- 先验证是否值得处理：
  - `book.tidb.io`
  - `pingcap.com`
  - `docs.pingcap.com`
  - `stackoverflow.com`
  - `rfc-editor.org`
  - `medium.com`
  - `dl.acm.org`

### 优先级 4

- 让 `signal_audit.json` 里的“已采未用”字段开始产生可控收益
- 先从：
  - `status_code`
  - `code_languages`
  - `nav_text`
- 开始，避免一次动太多维度。

## 下次具体执行建议

### 路线 A: 先做聚类命名清理

适合当前状态，收益最快。

步骤：

1. 改 `scripts/5_cluster_bookmarks.py` 的 representative token / cluster label 过滤。
2. 增补作者名、平台 slogan、过泛词的噪声抑制测试。
3. 只重跑 `4/5/6`。
4. 对比：
   - `quality_report.json`
   - `rule_suggestions.json`
   - `signal_audit.json`

成功标准：

- `Fetched` 类操作态词不再出现
- `发现主题` 候选更少、更稳定
- `create_topic` 建议更像真实技术主题

### 路线 B: 先做窄规则补齐

步骤：

1. 根据 `rule_suggestions.json` 选 3 到 5 个最干净的候选。
2. 加到 `data/category_rules_overrides.json` 或主规则文件。
3. 增补回归测试。
4. 重跑 `4/5/6`。

成功标准：

- `rule_gap` 下降
- `待整理` 下降
- 不引入新的 `mixed_cluster_count` 上升

### 路线 C: 先做抓取热点专项

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

成功标准：

- `待审阅异常` 继续下降
- `trusted_override` 不异常膨胀
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
- HTML 输出：`output/organized_bookmarks.html`
- 重复报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 待审阅报告：`output/reports/review_queue.json`
- 质量报告：`output/reports/quality_report.json`
- 规则建议：`output/reports/rule_suggestions.json`
- 信号审计：`output/reports/signal_audit.json`

## 一句话状态总结

项目已经从“能跑测试”升级到“能对真实书签稳定工作”，当前最大短板不再是抓取崩溃，而是 discovery / generic-platform 命名噪声、规则缺口和少量热点域名的抓取专项处理。
