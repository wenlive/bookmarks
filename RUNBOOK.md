# 运行手册

## 适用场景

这份手册面向日常使用，重点说明：

- 第一次从 Chrome 导出的书签文件生成整理结果
- 第一次忘记开代理，如何补跑
- 什么时候需要强制重新抓取全部网页
- 什么时候只清理抓取缓存，什么时候删除全部中间产物
- 导入生成的 HTML 后，如何审阅异常链接

## 推荐运行方式

如果你的网络环境需要代理，推荐在 shell 中先设置：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

然后执行：

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

如果当前网络不需要代理，则可以直接执行：

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## 常见操作

### 1. 第一次运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

输出重点看：

- `output/organized_bookmarks.html`
- `output/reports/broken_links.json`
- `output/reports/needs_confirmation.json`
- `output/reports/review_queue.json`
- `output/reports/rule_suggestions.json`
- `output/reports/quality_report.json`

### 2. 忘记开代理后补跑

如果第一次运行时忘记启用代理，不需要清空缓存，也不需要重头删除中间文件。

直接重新执行：

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

当前实现会：

- 复用 `data/bookmarks_with_info.json` 中已经成功抓取的书签
- 只重试 `timeout`、`error`、`broken`、`skipped` 这些非成功项
- 对 `知乎 / CSDN / GitHub / GitBook` 等受信任站点的常见反爬响应直接判为可继续使用
- 重新生成后续分类、聚类和 HTML 导出结果

### 3. 只重跑抓取及后续步骤

如果书签源文件没变，只想重新抓取失败项并刷新最终结果，可以分步执行：

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

### 4. 强制重新抓取全部网页

仅在以下情况建议这么做：

- 你怀疑之前的抓取结果整体不可信
- 站点内容变化较大，想全部刷新
- 想故意忽略历史成功缓存

命令：

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --force-refetch
```

如果同时需要代理：

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --force-refetch --use-proxy --trust-env
```

### 5. 删除抓取缓存后重新抓取

适合下面这些情况：

- 只想丢掉 `data/bookmarks_with_info.json`
- 想保留解析、分类、输出路径配置，但重新抓一遍网页
- 想避免旧的网页抓取结果影响新的信任策略

命令：

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

### 6. 删除全部中间产物和输出后重建

适合下面这些情况：

- 想完全从零开始
- 改了大量分类/聚类配置
- 想确保旧的聚类结果和报告文件全部失效

命令：

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

## 如何理解输出

### `organized_bookmarks.html`

这是最终导出的 Chrome 可导入书签文件。

### `review_queue.json`

这是统一待审阅异常报告，覆盖：

- 访问超时
- DNS/连接失败
- 证书异常
- HTTP 4xx/5xx
- 无效链接/非HTTP
- 其他抓取异常

默认情况下，`知乎 / CSDN / GitHub / GitBook` 等高频站点的常见反爬响应不会进入这里。

### `rule_suggestions.json`

这是规则改进建议报告。它会把未被现有规则稳定覆盖、但已经在聚类中形成主题的内容列出来，例如建议创建新主题、补充别名、补充专属域名或拆分混杂簇。

### `quality_report.json`

这是分类质量报告，重点看：

- `folder_only_classification_count` 是否为 0；
- `low_confidence_normal_category_count` 是否为 0；
- `generic_platform_domain_suggestion_count` 是否为 0；
- `largest_generic_platform_cluster_size` 是否过大；
- `largest_discovery_clusters` 和 `largest_tidy_clusters` 中是否存在需要补规则的主题。

如果 `largest_generic_platform_cluster_size` 很大，通常说明 `GitHub / CSDN / 知乎 / StackOverflow` 等通用平台仍有导航词或来源词污染聚类，需要优先拆簇或降权，而不是把这些平台域名加入某个主题规则。

### 为什么导出后的总书签数会变多

这是当前实现的设计结果，不是 bug。

异常链接会同时出现在：

- 原来的整理分类里
- 顶层 `待审阅` 目录里

因此 HTML 中的总书签数可能大于原始书签数。

## 导入后建议怎么审阅

导入 Chrome 后，优先查看：

1. `待审阅`
2. `待整理`
3. `发现主题`
4. 主分类中你最常用的目录

建议处理方式：

- `HTTP 4xx/5xx`：大概率已经失效，优先清理
- `证书异常`：确认站点是否仍可信
- `DNS/连接失败`：确认是否是站点迁移、域名变更或网络环境问题
- `访问超时`：可在网络更稳定时再次补跑

当前导出会优先把顶层目录合并成更少的展示分组，并尽量保留可读的叶子分类名称，减少书签栏最外层目录数量。
旧 Chrome 文件夹路径只保留为上下文，不再作为主题分类强证据；这会让不确定内容更多进入 `待整理`，但能显著降低错误归入常用主题目录的概率。
当前分类和聚类会共享结构化 `signal_pack`，优先消费用户备注、OG/Twitter 元信息、schema 类型、主正文、语言、canonical URL 和收藏时间桶；这比直接拼接网页标题和关键词更稳定。

## 环境建议

如果某个 Python/conda 环境存在 SSL 证书链或联网问题，不建议直接用于网页抓取。

至少应确保下面这类请求可以正常工作：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)"
```

如果这一步失败，优先修复 Python 环境，再运行本项目。
