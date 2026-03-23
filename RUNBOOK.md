# 运行手册

## 适用场景

这份手册面向日常使用，重点说明：

- 第一次从 Chrome 导出的书签文件生成整理结果
- 第一次忘记开代理，如何补跑
- 什么时候需要强制重新抓取全部网页
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

### 2. 忘记开代理后补跑

如果第一次运行时忘记启用代理，不需要清空缓存，也不需要重头删除中间文件。

直接重新执行：

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

当前实现会：

- 复用 `data/bookmarks_with_info.json` 中已经成功抓取的书签
- 只重试 `timeout`、`error`、`broken`、`skipped` 这些非成功项
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

### 为什么导出后的总书签数会变多

这是当前实现的设计结果，不是 bug。

异常链接会同时出现在：

- 原来的整理分类里
- 顶层 `待审阅` 目录里

因此 HTML 中的总书签数可能大于原始书签数。

## 导入后建议怎么审阅

导入 Chrome 后，优先查看：

1. `待审阅`
2. `待确认`
3. 主分类中你最常用的目录

建议处理方式：

- `HTTP 4xx/5xx`：大概率已经失效，优先清理
- `证书异常`：确认站点是否仍可信
- `DNS/连接失败`：确认是否是站点迁移、域名变更或网络环境问题
- `访问超时`：可在网络更稳定时再次补跑

## 环境建议

如果某个 Python/conda 环境存在 SSL 证书链或联网问题，不建议直接用于网页抓取。

至少应确保下面这类请求可以正常工作：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)"
```

如果这一步失败，优先修复 Python 环境，再运行本项目。
