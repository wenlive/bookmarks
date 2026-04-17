# 快速参考指南

## 一键运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## 一键运行并启用代理

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

## 分步运行

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 只重试失败抓取项

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 强制全量重抓

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --force-refetch
```

## 清理抓取缓存后重抓

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

## 删除全部中间产物并重建

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

## 关键输出

- 输出 HTML：`output/organized_bookmarks.html`
- 重复 URL 报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 待审阅异常报告：`output/reports/review_queue.json`
- 日志：`logs/bookmarks_organizer.log`

## 注意

- 抓取步骤默认不会自动启用代理，需显式传 `--use-proxy` 或在配置中开启。
- 重新执行时，已成功抓取的书签会优先复用缓存，只重试失败项。
- `知乎 / CSDN / GitHub / GitBook` 等受信任站点的常见反爬响应默认不进入 `待审阅`。
- 导出结果会先按展示分组组织顶层目录，再保留更细的叶子分类。
- 异常链接会保留在原分类中，并镜像到顶层 `待审阅` 目录，因此导出 HTML 中的总书签数可能大于原始书签数。

## 最小目录示意

```text
书签栏
├── 数据库
│   ├── PostgreSQL
│   └── TiDB
└── 待审阅
    ├── HTTP 4xx/5xx
    ├── DNS/连接失败
    └── 证书异常
```

## 快速检查

```bash
python3 -m compileall scripts tests
pytest
```
