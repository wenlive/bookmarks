import json
import asyncio
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_pipeline_support import build_metadata, write_enriched_fixture  # noqa: E402
from scripts_compat import common_module, parse_bookmarks_module, classify_module, cluster_module, html_module, copy_module, fetch_module, reset_module, bootstrap_module, apply_taxonomy_module, followup_module  # noqa: E402


def _bookmark(index, *, name, url, domain, category, folder, resource_type="文档", title="", description="", keywords="", content_preview=""):
    return {
        "id": f"bookmark_{index}",
        "name": name,
        "url": url,
        "domain": domain,
        "original_folder_path": folder,
        "metadata": {
            "title": title or name,
            "description": description,
            "keywords": keywords,
            "content_preview": content_preview or description,
            "resource_type": resource_type,
        },
        "classification": {
            "category": category,
            "all_scores": {
                category: {"total": 95},
            },
        },
    }


def test_parse_bookmarks_deduplicates_urls():
    sample = ROOT / "tests" / "fixtures" / "sample_bookmarks.html"
    result = parse_bookmarks_module.parse_bookmarks(sample)
    assert result["stats"]["total_bookmarks"] == 2
    assert result["stats"]["duplicate_count"] == 1
    assert result["stats"]["duplicates"][0]["url"] == "https://docs.python.org/3/"
    assert "docs.python.org" in result["stats"]["top_domains"]


def test_parse_bookmarks_normalizes_url_identity_but_keeps_distinct_fragments(tmp_path):
    sample = tmp_path / "sample.html"
    sample.write_text(
        """
        <!DOCTYPE NETSCAPE-Bookmark-file-1>
        <DL><p>
          <DT><A HREF="https://Example.com/docs#intro">Intro</A>
          <DT><A HREF="https://example.com/docs#intro">Intro Duplicate</A>
          <DT><A HREF="https://example.com/docs#advanced">Advanced</A>
        </DL><p>
        """,
        encoding="utf-8",
    )

    result = parse_bookmarks_module.parse_bookmarks(sample)

    assert result["stats"]["total_bookmarks"] == 2
    assert result["stats"]["duplicate_count"] == 1
    assert {bookmark["url"] for bookmark in result["bookmarks"]} == {
        "https://Example.com/docs#intro",
        "https://example.com/docs#advanced",
    }
    assert {bookmark["fetch_normalized_url"] for bookmark in result["bookmarks"]} == {
        "https://example.com/docs",
    }


def test_parse_bookmarks_preserves_user_description_attributes(tmp_path):
    sample = tmp_path / "sample.html"
    sample.write_text(
        """
        <!DOCTYPE NETSCAPE-Bookmark-file-1>
        <DL><p>
          <DT><A HREF="https://example.com/a" ADD_DATE="1700000000" DESCRIPTION="user note">A</A>
          <DT><A HREF="https://example.com/b" NOTES="manual context">B</A>
        </DL><p>
        """,
        encoding="utf-8",
    )

    result = parse_bookmarks_module.parse_bookmarks(sample)

    assert result["bookmarks"][0]["description"] == "user note"
    assert result["bookmarks"][1]["notes"] == "manual context"


def test_signal_pack_uses_structured_page_signals_and_schema_facets():
    bookmark = {
        "id": "bookmark_signal",
        "name": "Saved API Note",
        "url": "https://docs.example.com/ref",
        "domain": "docs.example.com",
        "add_date": "1700000000",
        "description": "user supplied context",
        "metadata": {
            "title": "Noisy HTML Title - Example",
            "description": "plain meta description",
            "page_signals": {
                "og:title": "Clean OG Title",
                "twitter:title": "Twitter Title",
                "og:description": "Clean OG description",
                "main_text_preview": "API reference body with concrete examples",
                "page_type_hints": ["documentation"],
                "schema_types": ["TechArticle"],
                "lang": "en",
            },
            "site_signals": {"site_name": "Example Docs", "brand_terms": ["Example"]},
        },
    }

    signal_pack = common_module.build_signal_pack(bookmark)

    assert signal_pack["schema_version"] == common_module.SIGNAL_PACK_SCHEMA_VERSION
    assert signal_pack["preferred_title"] == "Saved API Note"
    assert "Clean OG Title" in signal_pack["title_candidates"]
    assert signal_pack["preferred_description"] == "user supplied context"
    assert "文档" in signal_pack["resource_facets"]
    assert "博客" in signal_pack["resource_facets"]
    assert signal_pack["language"] == "en"
    assert signal_pack["time_bucket"]["year_month"] == "2023-11"
    assert "Clean OG Title" in signal_pack["semantic_text"]
    assert signal_pack["content"]["keywords_text"] == ""
    assert signal_pack["structure"]["site_name"] == "Example Docs"
    assert "identity.canonical_identity" in common_module.flatten_signal_pack(signal_pack, include_empty=True)


def test_copy_step_is_noop_for_same_file(tmp_path):
    source = tmp_path / "bookmarks.html"
    source.write_text("demo", encoding="utf-8")
    copied = copy_module.copy_bookmark_file(source, source)
    assert copied == source.resolve()
    assert source.read_text(encoding="utf-8") == "demo"


def test_classifier_outputs_multidimensional_labels_and_confirmation_report(tmp_path):
    classifier = classify_module.BookmarkClassifier(classification_options={"confirm_threshold": 90, "title_weight": 50})
    bookmark = {
        "id": "bookmark_1",
        "name": "收藏",
        "url": "https://www.python.org/dev/",
        "domain": "www.python.org",
        "original_folder_path": ["学习"],
        "metadata": build_metadata(
            "Python Packaging Guide", "", "python,packaging", "Python", "packaging docs",
            page_type_hints=["documentation"], site_name="Python", brand_terms=["python"],
        ),
    }
    classification = classifier.classify_bookmark(bookmark)
    assert classification["category"] == "待整理"
    assert classification["primary_topics"] == []
    assert classification["resource_type"] in {"文档", "教程"}
    assert "学习" in classification["intent_labels"]
    assert classification["score"] == 0
    assert "classification_evidence" in classification
    assert classification["display_category"] == classification["category"]
    assert classification["rule_candidates"] == []
    assert classification["rule_roots"] == []
    assert classification["rule_confidence"] == 0
    assert classification["cluster_hints"]

    bookmark["classification"] = classification
    report = tmp_path / "needs_confirmation.json"
    classify_module.export_confirmation_report([bookmark], report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    assert exported["count"] == 1
    assert exported["bookmarks"][0]["primary_topics"] == []


def test_classifier_ignores_fetch_operational_terms_in_cluster_hints():
    classifier = classify_module.BookmarkClassifier(classification_options={"confirm_threshold": 90, "title_weight": 50})
    metadata = build_metadata(
        "Mini-LSM Overview",
        "LSM-tree implementation notes",
        "lsm,storage",
        "Mini-LSM",
        "LSM storage internals",
        page_type_hints=["documentation"],
        site_name="Mini-LSM",
        brand_terms=["Mini", "LSM"],
    )
    metadata["site_signals"]["homepage_fetch_status"] = "success"
    metadata["site_signals"]["homepage_source"] = "fetched"
    bookmark = {
        "id": "bookmark_fetch_terms",
        "name": "Mini-LSM Overview",
        "url": "https://example.com/mini-lsm/overview",
        "domain": "example.com",
        "original_folder_path": ["数据库"],
        "metadata": metadata,
    }

    classification = classifier.classify_bookmark(bookmark)

    normalized_hints = {hint.lower() for hint in classification["cluster_hints"]}
    assert "success" not in normalized_hints
    assert "fetched" not in normalized_hints


def test_cluster_and_generate_html():
    bookmarks = [
        {
            "id": f"bookmark_{index}",
            "name": f"Python Article {index}",
            "url": f"https://docs.python.org/{index}",
            "domain": "docs.python.org",
            "metadata": build_metadata(
                "Python Documentation", "python guide", "python", "Python", "python docs",
                page_type_hints=["documentation"], site_name="Python", brand_terms=["python"],
            ),
            "classification": {"category": "编程语言/Python", "all_scores": {"编程语言/Python": {"total": 90}}},
        }
        for index in range(12)
    ]
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=3)
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程语言/Python", threshold=5)
    html = html_module.BookmarkHTMLGenerator().generate_html({"编程语言/Python": hierarchy})
    assert "NETSCAPE-Bookmark-file-1" in html
    assert "编程语言/Python" in html


def test_generate_html_supports_recursive_nodes():
    hierarchy = {
        "编程语言/Python": {
            "name": "编程语言/Python",
            "node_type": "topic",
            "count": 3,
            "bookmarks": [],
            "children": [
                {
                    "name": "Web 开发",
                    "node_type": "topic",
                    "count": 3,
                    "bookmarks": [],
                    "children": [
                        {
                            "name": "FastAPI",
                            "node_type": "topic",
                            "count": 2,
                            "bookmarks": [
                                {"name": "FastAPI Docs", "url": "https://fastapi.tiangolo.com/"},
                                {"name": "FastAPI Tutorial", "url": "https://example.com/fastapi"},
                            ],
                            "children": [],
                        },
                        {
                            "name": "Flask",
                            "node_type": "topic",
                            "count": 1,
                            "bookmarks": [
                                {"name": "Flask Docs", "url": "https://flask.palletsprojects.com/"},
                            ],
                            "children": [],
                        },
                    ],
                }
            ],
        }
    }
    html = html_module.BookmarkHTMLGenerator().generate_html(hierarchy)
    assert html.count("<H3") >= 4
    assert "Web 开发" in html
    assert "FastAPI" in html
    assert "Flask Docs" in html


def test_fetch_normalize_metadata_classifies_review_categories():
    timeout_md = fetch_module.normalize_metadata({"fetch_status": "timeout", "error": "Request timeout"})
    assert timeout_md["link_health"]["reason_label"] == "访问超时"

    cert_md = fetch_module.normalize_metadata(
        {
            "fetch_status": "error",
            "error": "Cannot connect to host learn.pingcap.com:443 ssl:True [SSLCertVerificationError: certificate verify failed: Hostname mismatch]",
        }
    )
    assert cert_md["link_health"]["reason_label"] == "证书异常"

    dns_md = fetch_module.normalize_metadata(
        {
            "fetch_status": "error",
            "error": "Cannot connect to host book.tidb.io:443 ssl:default [nodename nor servname provided, or not known]",
        }
    )
    assert dns_md["link_health"]["reason_label"] == "DNS/连接失败"


def test_parse_response_soup_uses_xml_parser_for_xml_content():
    xml = """<?xml version="1.0" encoding="utf-8"?><feed><title>XML Feed</title><entry><title>Item</title></entry></feed>"""
    soup = fetch_module.parse_response_soup(xml, {"content-type": "application/xml; charset=utf-8"})
    assert soup.find("feed") is not None
    assert soup.find("entry").find("title").get_text(strip=True) == "Item"

    broken_md = fetch_module.normalize_metadata({"fetch_status": "broken", "status_code": 404, "error": "HTTP 404"})
    assert broken_md["link_health"]["reason_label"] == "HTTP 4xx/5xx"


def test_trusted_access_policy_skips_review_and_reports_for_selected_domains(tmp_path):
    review_policy = {
        "trusted_access": {
            "enabled": True,
            "domain_suffixes": ["zhihu.com", "csdn.net", "github.com", "gitbook.com"],
            "http_statuses": [403, 429],
            "allow_reason_codes": ["timeout", "certificate", "other_error"],
            "domain_rules": [
                {
                    "domain_suffixes": ["csdn.net", "csdn.com", "csdnimg.cn"],
                    "http_statuses": [403, 404, 429, 520, 521, 522, 523, 524],
                }
            ],
        }
    }

    trusted = fetch_module.apply_review_policy(
        {"fetch_status": "broken", "status_code": 403, "error": "HTTP 403"},
        "zhuanlan.zhihu.com",
        review_policy,
    )
    assert trusted["link_health"]["reason_code"] == "trusted_access"
    assert trusted["link_health"]["review_required"] is False
    assert trusted["link_health"]["trusted_override"] is True
    assert trusted["link_health"]["raw_reason_code"] == "http_error"

    timeout = fetch_module.apply_review_policy(
        {"fetch_status": "timeout", "error": "Request timeout"},
        "blog.csdn.net",
        review_policy,
    )
    assert timeout["link_health"]["trusted_override"] is True

    trusted_csdn = fetch_module.apply_review_policy(
        {"fetch_status": "broken", "status_code": 521, "error": "HTTP 521"},
        "blog.csdn.net",
        review_policy,
    )
    assert trusted_csdn["link_health"]["trusted_override"] is True
    assert trusted_csdn["link_health"]["review_required"] is False

    github_missing = fetch_module.apply_review_policy(
        {"fetch_status": "broken", "status_code": 404, "error": "HTTP 404"},
        "github.com",
        review_policy,
    )
    assert github_missing["link_health"]["review_required"] is True

    dns_failure = fetch_module.apply_review_policy(
        {
            "fetch_status": "error",
            "error": "Cannot connect to host blog.csdn.net:443 ssl:default [nodename nor servname provided, or not known]",
        },
        "blog.csdn.net",
        review_policy,
    )
    assert dns_failure["link_health"]["review_required"] is True

    bookmark = {"id": "trusted", "name": "知乎文章", "url": "https://zhuanlan.zhihu.com/p/1", "domain": "zhuanlan.zhihu.com", "metadata": trusted}
    review_report = tmp_path / "review.json"
    broken_report = tmp_path / "broken.json"
    assert fetch_module.export_review_report([bookmark], review_report, review_policy) == 0
    assert fetch_module.export_broken_links_report([bookmark], broken_report, review_policy) == 0


def test_fetch_step_reuses_successful_cache_and_retries_failures(tmp_path):
    input_file = tmp_path / "parsed.json"
    output_file = tmp_path / "enriched.json"
    input_bookmarks = {
        "bookmarks": [
            {"id": "a", "name": "A", "url": "https://example.com/a", "domain": "example.com", "original_folder_path": ["A"]},
            {"id": "b", "name": "B", "url": "https://example.com/b", "domain": "example.com", "original_folder_path": ["B"]},
        ]
    }
    input_file.write_text(json.dumps(input_bookmarks, ensure_ascii=False), encoding="utf-8")
    cached = {
        "bookmarks": [
            {
                "id": "a",
                "name": "A",
                "url": "https://example.com/a",
                "domain": "example.com",
                "original_folder_path": ["A"],
                "metadata": build_metadata("Cached", "", "", "Cached", "Cached"),
            },
            {
                "id": "b",
                "name": "B",
                "url": "https://example.com/b",
                "domain": "example.com",
                "original_folder_path": ["B"],
                "metadata": fetch_module.normalize_metadata({"fetch_status": "timeout", "error": "Request timeout"}),
            },
        ]
    }
    output_file.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    calls = []

    async def fake_process_batch(bookmarks, session, timeout, max_retries, proxy_options, review_policy=None):
        calls.extend(bookmark["id"] for bookmark in bookmarks)
        return [
            {
                **bookmark,
                "metadata": build_metadata("Fetched", "", "", "Fetched", "Fetched"),
            }
            for bookmark in bookmarks
        ]

    original = fetch_module.process_batch
    fetch_module.process_batch = fake_process_batch
    try:
        result = asyncio.run(
            fetch_module.fetch_webpage_info_async(
                input_file,
                output_file,
                {
                    "concurrent_limit": 5,
                    "timeout": 1,
                    "delay": 0,
                    "batch_size": 10,
                    "max_retries": 0,
                    "force_refetch": False,
                    "user_agent": "test-agent",
                    "proxy": {"enabled": False, "trust_env": False, "http_proxy": None, "https_proxy": None, "all_proxy": None},
                },
                common_module.configure_logging(common_module.PipelineConfig.load(ROOT / "skill_config.json"), "INFO"),
            )
        )
    finally:
        fetch_module.process_batch = original

    assert calls == ["b"]
    assert result["stats"]["reused_count"] == 1
    assert result["stats"]["retried_count"] == 1
    assert result["bookmarks"][0]["metadata"]["title"] == "Cached"
    assert result["bookmarks"][1]["metadata"]["title"] == "Fetched"


def test_run_fetch_passes_retries_without_proxy_when_requested(tmp_path):
    input_file = tmp_path / "parsed.json"
    output_file = tmp_path / "enriched.json"
    input_file.write_text(json.dumps({"bookmarks": []}, ensure_ascii=False), encoding="utf-8")
    calls: list[str] = []

    async def fake_fetch(input_path, output_path, options, logger):
        route = "proxy" if fetch_module.fetch_route_configured(options.get("proxy", {})) else "direct"
        calls.append(route)
        return {
            "bookmarks": [],
            "stats": {
                "total_bookmarks": 0,
                "success_count": 1 if route == "proxy" else 2,
                "broken_count": 0,
                "fail_count": 0,
                "review_free_count": 1 if route == "proxy" else 2,
                "reused_count": 0 if route == "proxy" else 1,
                "retried_count": 1 if route == "proxy" else 0,
                "trusted_override_count": 0,
                "proxy_enabled": route == "proxy",
                "proxy_trust_env": route == "proxy",
                "route_counts": {route: 1},
                "review_by_route": {},
            },
        }

    original = fetch_module.fetch_webpage_info_async
    fetch_module.fetch_webpage_info_async = fake_fetch
    try:
        result = fetch_module.run_fetch_passes(
            input_file,
            output_file,
            {
                "concurrent_limit": 1,
                "timeout": 1,
                "delay": 0,
                "batch_size": 1,
                "max_retries": 0,
                "force_refetch": False,
                "user_agent": "pytest",
                "direct_retry_after_proxy": True,
                "proxy": {
                    "enabled": True,
                    "trust_env": True,
                    "http_proxy": None,
                    "https_proxy": None,
                    "all_proxy": None,
                },
            },
            common_module.configure_logging(common_module.PipelineConfig.load(ROOT / "skill_config.json"), "INFO"),
        )
    finally:
        fetch_module.fetch_webpage_info_async = original

    assert calls == ["proxy", "direct"]
    assert result["stats"]["multi_pass_mode"] == "proxy_then_direct_retry"
    assert result["stats"]["proxy_enabled"] is True
    assert result["stats"]["proxy_trust_env"] is True
    assert result["stats"]["pass_deltas"]["success_delta"] == 1
    assert [item["name"] for item in result["stats"]["pass_summaries"]] == ["proxy", "direct_retry"]


def test_fetch_step_reuses_trusted_cached_result_without_retry(tmp_path):
    input_file = tmp_path / "parsed.json"
    output_file = tmp_path / "enriched.json"
    input_bookmarks = {
        "bookmarks": [
            {"id": "trusted", "name": "知乎文章", "url": "https://zhuanlan.zhihu.com/p/1", "domain": "zhuanlan.zhihu.com", "original_folder_path": ["知乎"]},
        ]
    }
    input_file.write_text(json.dumps(input_bookmarks, ensure_ascii=False), encoding="utf-8")
    cached = {
        "bookmarks": [
            {
                "id": "trusted",
                "name": "知乎文章",
                "url": "https://zhuanlan.zhihu.com/p/1",
                "domain": "zhuanlan.zhihu.com",
                "original_folder_path": ["知乎"],
                "metadata": fetch_module.normalize_metadata({"fetch_status": "broken", "status_code": 403, "error": "HTTP 403"}),
            }
        ]
    }
    output_file.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    calls = []

    async def fake_process_batch(bookmarks, session, timeout, max_retries, proxy_options, review_policy=None):
        calls.extend(bookmark["id"] for bookmark in bookmarks)
        return []

    original = fetch_module.process_batch
    fetch_module.process_batch = fake_process_batch
    try:
        result = asyncio.run(
            fetch_module.fetch_webpage_info_async(
                input_file,
                output_file,
                {
                    "concurrent_limit": 5,
                    "timeout": 1,
                    "delay": 0,
                    "batch_size": 10,
                    "max_retries": 0,
                    "force_refetch": False,
                    "user_agent": "test-agent",
                    "proxy": {"enabled": False, "trust_env": False, "http_proxy": None, "https_proxy": None, "all_proxy": None},
                    "review_policy": {
                        "trusted_access": {
                            "enabled": True,
                            "domain_suffixes": ["zhihu.com"],
                            "http_statuses": [403],
                            "allow_reason_codes": [],
                        }
                    },
                },
                common_module.configure_logging(common_module.PipelineConfig.load(ROOT / "skill_config.json"), "INFO"),
            )
        )
    finally:
        fetch_module.process_batch = original

    assert calls == []
    assert result["stats"]["reused_count"] == 1
    assert result["stats"]["retried_count"] == 0
    assert result["stats"]["trusted_override_count"] == 1
    assert result["bookmarks"][0]["metadata"]["link_health"]["review_required"] is False


def test_fetch_step_reuses_cache_across_id_changes_and_preserves_bookmark_rows(tmp_path):
    input_file = tmp_path / "parsed.json"
    output_file = tmp_path / "enriched.json"
    input_bookmarks = {
        "bookmarks": [
            {
                "id": "new-a",
                "name": "Section A",
                "url": "https://example.com/docs#a",
                "fetch_normalized_url": "https://example.com/docs",
                "domain": "example.com",
                "original_folder_path": ["Docs"],
            },
            {
                "id": "new-b",
                "name": "Section B",
                "url": "https://example.com/docs#b",
                "fetch_normalized_url": "https://example.com/docs",
                "domain": "example.com",
                "original_folder_path": ["Docs"],
            },
        ]
    }
    input_file.write_text(json.dumps(input_bookmarks, ensure_ascii=False), encoding="utf-8")
    cached = {
        "bookmarks": [
            {
                "id": "old-id",
                "name": "Legacy",
                "url": "https://example.com/docs",
                "fetch_normalized_url": "https://example.com/docs",
                "domain": "example.com",
                "original_folder_path": ["Docs"],
                "metadata": build_metadata("Cached Docs", "", "", "Cached Docs", "Cached Docs"),
            }
        ]
    }
    output_file.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    result = asyncio.run(
        fetch_module.fetch_webpage_info_async(
            input_file,
            output_file,
            {
                "concurrent_limit": 2,
                "timeout": 1,
                "delay": 0,
                "batch_size": 10,
                "max_retries": 0,
                "force_refetch": False,
                "user_agent": "test-agent",
                "proxy": {"enabled": False, "trust_env": False, "http_proxy": None, "https_proxy": None, "all_proxy": None},
            },
            common_module.configure_logging(common_module.PipelineConfig.load(ROOT / "skill_config.json"), "INFO"),
        )
    )

    assert result["stats"]["reused_count"] == 2
    assert result["stats"]["retried_count"] == 0
    assert [bookmark["name"] for bookmark in result["bookmarks"]] == ["Section A", "Section B"]
    assert [bookmark["url"] for bookmark in result["bookmarks"]] == [
        "https://example.com/docs#a",
        "https://example.com/docs#b",
    ]
    assert all(bookmark["metadata"]["title"] == "Cached Docs" for bookmark in result["bookmarks"])


def test_cluster_and_generate_html_include_review_hierarchy():
    bookmark = {
        "id": "broken_1",
        "name": "Broken Link",
        "url": "https://broken.example.com",
        "domain": "broken.example.com",
        "original_folder_path": ["Ops"],
        "metadata": fetch_module.normalize_metadata({"fetch_status": "broken", "status_code": 404, "error": "HTTP 404"}),
        "classification": {
            "category": "运维/工具",
            "all_scores": {"运维/工具": {"total": 88}},
            "review_required": True,
            "review_category": "HTTP 4xx/5xx",
        },
    }
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    hierarchy = clusterer.build_hierarchy([bookmark], "运维/工具", threshold=5)
    payload = {
        "hierarchy": {"运维/工具": hierarchy},
        "review_hierarchy": {
            "待审阅": {
                "category": "待审阅",
                "subcategories": {
                    "待审阅/HTTP 4xx/5xx": {
                        "bookmarks": [bookmark],
                        "count": 1,
                    }
                },
                "bookmarks": [],
                "count": 1,
            }
        },
    }
    html = html_module.BookmarkHTMLGenerator().generate_html(payload["hierarchy"], payload["review_hierarchy"])
    assert "待审阅" in html
    assert "HTTP 4xx/5xx" in html
    assert "Broken Link" in html


def test_optimize_tree_collapses_single_child_and_merges_others():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=3, merge_small_nodes_threshold=2)
    node = {
        "name": "Root",
        "node_type": "topic",
        "bookmarks": [],
        "children": [
            {
                "name": "Only Child",
                "node_type": "topic",
                "bookmarks": [],
                "children": [
                    {
                        "name": "Only Grandchild",
                        "node_type": "topic",
                        "bookmarks": [{"name": "Deep Link", "url": "https://example.com/deep"}],
                        "children": [],
                        "count": 1,
                    }
                ],
                "count": 1,
            },
            {
                "name": "Tiny",
                "node_type": "topic",
                "bookmarks": [{"name": "Tiny Link", "url": "https://example.com/tiny"}],
                "children": [],
                "count": 1,
            },
        ],
        "count": 2,
    }
    optimized = clusterer.optimize_tree(node, is_root=True)
    assert optimized["children"][0]["name"] == "Only Grandchild"
    assert any(bookmark["name"] == "Tiny Link" for bookmark in optimized["bookmarks"])

def test_rich_feature_clustering_groups_cross_domain_same_topic():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/tutorial/", domain="fastapi.tiangolo.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api tutorial", keywords="python,fastapi,api"),
        _bookmark(2, name="FastAPI 部署指南", url="https://realpython.com/fastapi-deploy/", domain="realpython.com", category="编程/Python Web", folder=["学习", "Web"], resource_type="博客", description="python fastapi deployment web api", keywords="python,fastapi,deployment"),
        _bookmark(3, name="FastAPI 示例仓库", url="https://github.com/example/fastapi-service", domain="github.com", category="编程/Python Web", folder=["代码", "FastAPI"], resource_type="仓库", description="python fastapi service repository", keywords="python,fastapi,repository"),
    ]
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程/Python Web", threshold=1)
    assert len(hierarchy["subcategories"]) == 1
    assert hierarchy["count"] == 3
    only_cluster = next(iter(hierarchy["subcategories"].values()))
    assert only_cluster["count"] >= 2
    assert "fastapi" in only_cluster["representative_tokens"]
    assert only_cluster["cluster_reason"]
    assert only_cluster["merge_from_categories"] == ["编程/Python Web"]


def test_rich_feature_clustering_splits_same_domain_different_topics():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="OpenAI API Reference", url="https://platform.openai.com/docs/api-reference", domain="platform.openai.com", category="AI/LLM API", folder=["AI", "API"], resource_type="文档", description="openai api reference embeddings chat", keywords="openai,api,reference"),
        _bookmark(2, name="OpenAI Embeddings Guide", url="https://platform.openai.com/docs/guides/embeddings", domain="platform.openai.com", category="AI/LLM API", folder=["AI", "API"], resource_type="文档", description="openai embeddings api guide", keywords="openai,embeddings,api"),
        _bookmark(3, name="OpenAI Safety Research", url="https://platform.openai.com/research/safety", domain="platform.openai.com", category="AI/Research", folder=["AI", "Research"], resource_type="论文", description="alignment safety evaluation research", keywords="alignment,safety,research"),
        _bookmark(4, name="OpenAI Alignment Notes", url="https://platform.openai.com/research/alignment", domain="platform.openai.com", category="AI/Research", folder=["AI", "Research"], resource_type="论文", description="alignment evaluation interpretability research", keywords="alignment,interpretability,research"),
    ]
    hierarchy = clusterer.build_hierarchy(bookmarks, "AI", threshold=1)
    assert len(hierarchy["subcategories"]) == 2
    cluster_sizes = sorted(item["count"] for item in hierarchy["subcategories"].values())
    assert cluster_sizes == [2, 2]
    for item in hierarchy["subcategories"].values():
        assert item["source_folder_quality_score"] >= 0
        assert "cluster_reason" in item


def test_global_clustering_can_group_cross_category_bookmarks_before_root_assignment():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/", domain="fastapi.tiangolo.com", category="后端开发/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api", keywords="python,fastapi,api"),
        _bookmark(2, name="FastAPI 生产实践", url="https://blog.example.com/fastapi-prod", domain="blog.example.com", category="技术博客/API 实践", folder=["博客", "FastAPI"], resource_type="博客", description="fastapi production deployment", keywords="fastapi,deployment,api"),
        _bookmark(3, name="FastAPI Service Template", url="https://github.com/example/fastapi-service", domain="github.com", category="开源项目/Python 服务", folder=["代码", "FastAPI"], resource_type="仓库", description="fastapi service template repository", keywords="fastapi,service,repository"),
    ]
    for bookmark in bookmarks:
        bookmark["classification"].update(
            {
                "display_category": bookmark["classification"]["category"],
                "cluster_hints": ["FastAPI", "Python", "API"],
                "open_topic_candidates": [{"topic": "FastAPI", "score": 8, "sources": ["title", "keywords"]}],
                "rule_roots": [
                    {"root": bookmark["classification"]["category"].split("/")[0], "support": 0.45, "total": 45.0},
                    {"root": "开源项目", "support": 0.2, "total": 20.0},
                ],
                "rule_confidence": 0.42,
            }
        )

    cluster_profiles = cluster_module.build_cluster_payloads(clusterer, bookmarks, threshold=20, discovery_root_name="发现主题")

    assert len(cluster_profiles) == 1
    assert cluster_profiles[0]["cluster_label"] == "FastAPI"
    assert len(cluster_profiles[0]["bookmarks"]) == 3


def test_ambiguous_rule_roots_fall_back_to_discovery_root():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/", domain="fastapi.tiangolo.com", category="后端开发/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api", keywords="python,fastapi,api"),
        _bookmark(2, name="FastAPI 生产实践", url="https://blog.example.com/fastapi-prod", domain="blog.example.com", category="技术博客/API 实践", folder=["博客", "FastAPI"], resource_type="博客", description="fastapi production deployment", keywords="fastapi,deployment,api"),
        _bookmark(3, name="FastAPI Service Template", url="https://github.com/example/fastapi-service", domain="github.com", category="开源项目/Python 服务", folder=["代码", "FastAPI"], resource_type="仓库", description="fastapi service template repository", keywords="fastapi,service,repository"),
    ]
    weights = [0.4, 0.35, 0.3]
    for bookmark, weight in zip(bookmarks, weights):
        bookmark["classification"].update(
            {
                "display_category": bookmark["classification"]["category"],
                "cluster_hints": ["FastAPI", "Python", "API"],
                "open_topic_candidates": [{"topic": "FastAPI", "score": 8, "sources": ["title", "keywords"]}],
                "rule_roots": [
                    {"root": bookmark["classification"]["category"].split("/")[0], "support": weight, "total": weight * 100},
                    {"root": "工程工具与资源", "support": 0.2, "total": 20.0},
                ],
                "rule_confidence": 0.38,
            }
        )

    hierarchy = cluster_module.build_root_hierarchy(clusterer, bookmarks, threshold=20, discovery_root_name="发现主题")

    assert "发现主题" in hierarchy
    assert next(iter(hierarchy["发现主题"]["subcategories"].values()))["count"] == 3


def test_low_confidence_tidy_clusters_do_not_return_to_normal_roots():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="Maybe TiDB note", url="https://example.com/a", domain="example.com", category="待整理", folder=["TiDB"], resource_type="文档", description="tidb note", keywords="tidb"),
        _bookmark(2, name="Maybe TiDB article", url="https://example.com/b", domain="example.com", category="待整理", folder=["TiDB"], resource_type="文档", description="tidb article", keywords="tidb"),
    ]
    for bookmark in bookmarks:
        bookmark["classification"].update(
            {
                "display_category": "待整理",
                "cluster_hints": ["TiDB"],
                "open_topic_candidates": [{"topic": "TiDB", "score": 4, "sources": ["title"]}],
                "rule_roots": [{"root": "数据库", "support": 1.0, "total": 20.0}],
                "rule_confidence": 0.4,
            }
        )

    profiles = cluster_module.build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=20,
        discovery_root_name="发现主题",
        tidy_root_name="待整理",
    )

    assert profiles[0]["destination_root"] == "发现主题"
    assert profiles[0]["average_rule_confidence"] == 0.4
    assert profiles[0]["normal_category_support"] == 0


def test_generic_platform_domain_does_not_force_unrelated_repos_into_one_cluster():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2, generic_platform_domains={"github.com"})
    bookmarks = [
        _bookmark(1, name="TiDB storage engine", url="https://github.com/pingcap/tidb", domain="github.com", category="数据库/TiDB", folder=["old"], resource_type="仓库", description="tidb tikv distributed database", keywords="tidb,tikv,database"),
        _bookmark(2, name="TiKV raft kv store", url="https://github.com/tikv/tikv", domain="github.com", category="数据库/TiDB", folder=["old"], resource_type="仓库", description="tikv raft key value database", keywords="tikv,raft,database"),
        _bookmark(3, name="FastAPI service template", url="https://github.com/example/fastapi-service", domain="github.com", category="后端开发/Python Web", folder=["old"], resource_type="仓库", description="fastapi python web api service", keywords="fastapi,python,api"),
        _bookmark(4, name="FastAPI worker template", url="https://github.com/example/fastapi-worker", domain="github.com", category="后端开发/Python Web", folder=["old"], resource_type="仓库", description="fastapi python async worker", keywords="fastapi,python,async"),
    ]
    for bookmark in bookmarks:
        category = bookmark["classification"]["category"]
        root = category.split("/")[0]
        bookmark["classification"].update(
            {
                "cluster_hints": [category.split("/")[-1], root],
                "rule_roots": [{"root": root, "support": 1.0, "total": 90.0}],
                "rule_confidence": 0.85,
            }
        )

    clusters = clusterer.cluster_bookmarks(bookmarks)

    assert sorted(len(cluster) for cluster in clusters) == [2, 2]
    assert all(len({bookmark["classification"]["category"] for bookmark in cluster}) == 1 for cluster in clusters)


def test_clusterer_splits_large_high_confidence_mixed_categories():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(index, name=f"openGauss 文档 {index}", url=f"https://docs.opengauss.org/zh/docs/latest/doc{index}.html", domain="docs.opengauss.org", category="数据库/openGauss", folder=["数据库", "openGauss"], description="opengauss database documentation")
        for index in range(1, 4)
    ] + [
        _bookmark(index + 10, name=f"PostgreSQL 文档 {index}", url=f"https://www.postgresql.org/docs/current/doc{index}.html", domain="www.postgresql.org", category="数据库/PostgreSQL", folder=["数据库", "PostgreSQL"], description="postgresql database documentation")
        for index in range(1, 4)
    ]
    for bookmark in bookmarks:
        category = bookmark["classification"]["category"]
        bookmark["classification"].update(
            {
                "display_category": category,
                "cluster_hints": ["文档中心", "数据库"],
                "open_topic_candidates": [{"topic": category.split("/")[-1], "score": 8, "sources": ["title"]}],
                "rule_roots": [{"root": "数据库", "support": 1.0, "total": 90.0}],
                "rule_confidence": 0.92,
            }
        )

    clusterer._connected_components = lambda bookmarks, features, threshold=0.34: [bookmarks]
    clusterer._split_if_needed = lambda clusters: clusters
    clusterer._merge_if_needed = lambda clusters: clusters

    clusters = clusterer.cluster_bookmarks(bookmarks)

    assert sorted(len(cluster) for cluster in clusters) == [3, 3]
    assert all(len({bookmark["classification"]["category"] for bookmark in cluster}) == 1 for cluster in clusters)


def test_fetch_blocked_clusters_stay_in_tidy_and_suggest_fetch_investigation():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(index, name=f"TiDB 问题导图 {index}", url=f"https://docs.pingcap.com/tidb/stable/troubleshoot-{index}", domain="docs.pingcap.com", category="待整理", folder=["数据库", "TiDB"], description="tidb pingcap docs")
        for index in range(1, 5)
    ]
    for bookmark in bookmarks:
        bookmark["metadata"]["fetch_status"] = "error"
        bookmark["classification"].update(
            {
                "display_category": "待整理",
                "cluster_hints": ["TiDB", "Pingcap"],
                "open_topic_candidates": [{"topic": "Pingcap", "score": 6, "sources": ["title"]}],
                "rule_roots": [{"root": "数据库", "support": 1.0, "total": 24.0}],
                "rule_confidence": 0.4,
                "confirmation_bucket": "fetch_blocked",
                "review_required": True,
            }
        )

    clusterer._connected_components = lambda bookmarks, features, threshold=0.34: [bookmarks]
    clusterer._split_if_needed = lambda clusters: clusters
    clusterer._merge_if_needed = lambda clusters: clusters

    profiles = cluster_module.build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=20,
        discovery_root_name="发现主题",
        tidy_root_name="待整理",
    )
    suggestions = cluster_module.generate_rule_suggestions(
        profiles,
        discovery_root_name="发现主题",
        tidy_root_name="待整理",
    )

    assert profiles[0]["destination_root"] == "待整理"
    assert profiles[0]["fetch_blocked_share"] == 1.0
    assert suggestions["suggestions"][0]["type"] == "investigate_fetch_failures"


def test_generate_rule_suggestions_reports_low_purity_clusters():
    bookmarks = [
        {"id": f"bookmark_{index}", "name": f"FastAPI {index}", "url": f"https://example.com/{index}", "domain": "example.com", "classification": {"category": "其他/未分类"}}
        for index in range(4)
    ]
    report = cluster_module.generate_rule_suggestions(
        [
            {
                "cluster_id": "cluster_0001",
                "cluster_label": "FastAPI",
                "rule_purity": 0.22,
                "destination_root": "发现主题",
                "dominant_categories": [{"category": "其他/未分类", "root": "其他", "count": 4, "share": 1.0}],
                "top_domains": [{"domain": "fastapi.tiangolo.com", "count": 2, "share": 0.5}],
                "representative_tokens": ["fastapi", "python", "asgi"],
                "discovered_topics": ["FastAPI", "Python"],
                "bookmarks": bookmarks,
            }
        ],
        discovery_root_name="发现主题",
    )

    assert report["count"] == 1
    assert report["suggestions"][0]["cluster_label"] == "FastAPI"
    assert report["suggestions"][0]["type"] in {
        "add_specific_domain",
        "add_alias",
        "create_topic",
        "split_mixed_cluster",
        "demote_noisy_keyword_or_folder_signal",
    }


def test_high_quality_folder_reused_and_low_quality_folder_split():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    high_quality = [
        _bookmark(1, name="Django ORM Guide", url="https://docs.djangoproject.com/en/orm/", domain="docs.djangoproject.com", category="编程/Django", folder=["Backend", "Django"], resource_type="文档", description="django orm models querysets", keywords="django,orm,models"),
        _bookmark(2, name="Django Forms Guide", url="https://docs.djangoproject.com/en/forms/", domain="docs.djangoproject.com", category="编程/Django", folder=["Backend", "Django"], resource_type="文档", description="django forms validation", keywords="django,forms,validation"),
    ]
    high_hierarchy = clusterer.build_hierarchy(high_quality, "编程/Django", threshold=1)
    high_cluster = next(iter(high_hierarchy["subcategories"].values()))
    assert high_cluster["source_folder_reused"] is False
    assert high_cluster["source_folder_quality_score"] == 0.0

    low_quality = [
        _bookmark(3, name="Kubernetes 入门", url="https://kubernetes.io/docs/tutorials/", domain="kubernetes.io", category="运维/Kubernetes", folder=["杂项"], resource_type="文档", description="kubernetes cluster tutorial", keywords="kubernetes,cluster"),
        _bookmark(4, name="Figma 插件目录", url="https://www.figma.com/community/plugins", domain="www.figma.com", category="设计/Figma", folder=["杂项"], resource_type="工具", description="figma design plugin tools", keywords="figma,design,plugin"),
        _bookmark(5, name="Rust Ownership", url="https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html", domain="doc.rust-lang.org", category="编程/Rust", folder=["杂项"], resource_type="文档", description="rust ownership borrow checker", keywords="rust,ownership"),
        _bookmark(6, name="Vue Router", url="https://router.vuejs.org/guide/", domain="router.vuejs.org", category="前端/Vue", folder=["杂项"], resource_type="文档", description="vue router navigation", keywords="vue,router"),
    ]
    low_hierarchy = clusterer.build_hierarchy(low_quality, "混合", threshold=1)
    assert len(low_hierarchy["subcategories"]) >= 2
    for item in low_hierarchy["subcategories"].values():
        assert item["source_folder_reused"] is False
        assert item["source_folder_quality_score"] < 0.68 or item["count"] == 1


def test_optimize_tree_preserves_reference_folder_when_only_child_is_topic():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=3, merge_small_nodes_threshold=2)
    node = {
        "name": "编程语言/Python · docs.python.org",
        "node_type": "reference",
        "bookmarks": [],
        "children": [
            {
                "name": "fastapi",
                "node_type": "topic",
                "bookmarks": [
                    {"name": "FastAPI Docs", "url": "https://docs.python.org/fastapi"},
                ],
                "children": [],
                "count": 1,
            }
        ],
        "count": 1,
    }

    optimized = clusterer.optimize_tree(node)

    assert optimized["name"] == "编程语言/Python · docs.python.org"
    assert optimized["node_type"] == "reference"
    assert optimized["children"][0]["name"] == "fastapi"


def test_config_paths_are_resolved_relative_to_config_file(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "skill_config.json"
    config_file.write_text(
        json.dumps(
            {
                "input": {"bookmark_file": "inputs/bookmarks.html"},
                "output": {"reports_directory": "reports"},
                "logging": {"file": "logs/app.log", "console": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = common_module.PipelineConfig.load(config_file)
    assert config.paths.bookmark_file == (config_dir / "inputs" / "bookmarks.html").resolve()
    assert config.paths.log_file == (config_dir / "logs" / "app.log").resolve()
    assert config.paths.confirmation_report_file == (config_dir / "reports" / "needs_confirmation.json").resolve()


def test_default_config_has_no_rule_file_dependency_or_root_groups(tmp_path):
    raw = json.loads((ROOT / "skill_config.json").read_text(encoding="utf-8"))
    raw.setdefault("input", {})["user_taxonomy_file"] = str(tmp_path / "missing_user_taxonomy.json")
    raw["input"]["bookmark_assignment_file"] = str(tmp_path / "missing_assignments.json")
    config_file = tmp_path / "skill_config.json"
    config_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    config = common_module.PipelineConfig.load(config_file)

    assert config.clustering_options["root_groups"] == []
    assert config.clustering_options["display"]["grouping_mode"] == "auto"
    assert config.fetch_options["review_policy"]["trusted_access"]["domain_suffixes"] == []
    assert classify_module.DEFAULT_RULE_BUNDLE["categories"] == {}


def test_taxonomy_bootstrap_generates_prompt_and_cluster_payload(tmp_path):
    classified_file = tmp_path / "classified.json"
    reports_dir = tmp_path / "reports"
    config_file = tmp_path / "skill_config.json"
    bookmarks = [
        _bookmark(1, name="Python Packaging Guide", url="https://example.com/python-packaging", domain="example.com", category="待整理", folder=["Inbox"], description="python packaging docs", keywords="python,packaging"),
        _bookmark(2, name="Python Wheels", url="https://example.com/python-wheels", domain="example.com", category="待整理", folder=["Inbox"], description="python wheel format", keywords="python,wheel"),
        _bookmark(3, name="Rose Pruning", url="https://garden.example.com/rose", domain="garden.example.com", category="待整理", folder=["Inbox"], description="garden rose pruning", keywords="garden,rose"),
    ]
    for bookmark in bookmarks:
        bookmark["classification"].update(
            {
                "display_category": "待整理",
                "resource_type": "文档",
                "rule_confidence": 0.0,
                "cluster_hints": ["Python" if "Python" in bookmark["name"] else "Garden"],
                "confirmation_bucket": "rule_gap",
                "review_required": False,
            }
        )
    classified_file.write_text(
        json.dumps({"schema_version": common_module.CLASSIFIED_OUTPUT_SCHEMA_VERSION, "bookmarks": bookmarks}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_file.write_text(
        json.dumps(
            {
                "pipeline": {"classified_file": str(classified_file)},
                "output": {"reports_directory": str(reports_dir)},
                "clustering_options": {"min_cluster_size": 2, "max_bookmarks_without_clustering": 20},
                "logging": {"file": str(tmp_path / "app.log"), "console": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["python3", "scripts/generate_taxonomy_bootstrap.py", "--config", str(config_file), "--max-clusters", "5"],
        cwd=ROOT,
        check=True,
    )

    clusters = json.loads((reports_dir / "taxonomy_bootstrap_clusters.json").read_text(encoding="utf-8"))
    prompt = (reports_dir / "taxonomy_bootstrap_prompt.md").read_text(encoding="utf-8")
    assert clusters["schema_version"] == common_module.TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION
    assert clusters["clusters"]
    assert clusters["clusters"][0]["cluster_id"].startswith("bc_")
    assert "user_taxonomy_response/v1" in prompt
    assert "不要把 GitHub" in prompt


def test_taxonomy_followup_bundles_include_tidy_semantic_bundles():
    docker_docs = _bookmark(
        1,
        name="Docker Docs",
        url="https://docs.docker.com/engine/",
        domain="docs.docker.com",
        category="待整理",
        folder=["Inbox"],
        description="docker engine manual",
        keywords="docker,engine",
    )
    docker_blog = _bookmark(
        2,
        name="Docker Networking",
        url="https://example.com/docker-networking",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="docker networking guide",
        keywords="docker,networking",
    )
    openai_one = _bookmark(
        3,
        name="OpenAI API Guide",
        url="https://example.com/openai-api-guide",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="openai api quickstart",
        keywords="openai,api",
    )
    openai_two = _bookmark(
        4,
        name="OpenAI Embeddings",
        url="https://example.com/openai-embeddings",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="openai embeddings usage",
        keywords="openai,embeddings",
    )
    blocked = _bookmark(
        5,
        name="Blocked Topic",
        url="https://blocked.example.com/topic",
        domain="blocked.example.com",
        category="待整理",
        folder=["Inbox"],
        description="blocked content",
        keywords="blocked",
    )
    for bookmark, topic in (
        (docker_docs, "Docker"),
        (docker_blog, "Docker"),
        (openai_one, "OpenAI"),
        (openai_two, "OpenAI"),
        (blocked, "Blocked"),
    ):
        bookmark["classification"].update(
            {
                "display_category": "待整理",
                "resource_type": "文档",
                "rule_confidence": 0.0,
                "cluster_hints": [topic],
                "open_topic_candidates": [{"topic": topic, "confidence": 0.8}],
                "rule_candidates": [],
                "confirmation_bucket": "rule_gap",
                "review_required": bookmark["id"] == "bookmark_5",
            }
        )

    cluster_profiles = [
        {
            "cluster_id": "cluster_docker",
            "cluster_label": "Docker",
            "destination_root": "待整理",
            "bookmarks": [docker_docs, docker_blog],
        }
    ]

    bundles = followup_module.build_followup_bundles(
        [docker_docs, docker_blog, openai_one, openai_two, blocked],
        cluster_profiles,
        generic_platform_domains=set(common_module.DEFAULT_GENERIC_PLATFORM_DOMAINS),
        tidy_root_name="待整理",
    )

    assert {bundle["bundle_type"] for bundle in bundles} == {"cluster", "tidy_semantic"}
    assert any(bundle["cluster_label"] == "Docker" and bundle["support_count"] == 2 for bundle in bundles)
    assert any(bundle["bundle_type"] == "tidy_semantic" and bundle["cluster_label"] == "OpenAI" for bundle in bundles)
    assert any(bundle["bundle_type"] == "tidy_semantic" and bundle["cluster_id"].startswith("ts_") for bundle in bundles)
    bundled_identities = {identity for bundle in bundles for identity in bundle["bookmark_identities"]}
    assert "https://blocked.example.com/topic" not in bundled_identities


def test_apply_taxonomy_response_generates_files_and_classifier_uses_assignment(tmp_path):
    clusters_file = tmp_path / "taxonomy_bootstrap_clusters.json"
    response_file = tmp_path / "taxonomy_response.md"
    taxonomy_file = tmp_path / "generated" / "user_taxonomy.json"
    assignments_file = tmp_path / "generated" / "bookmark_taxonomy_assignments.json"
    config_file = tmp_path / "skill_config.json"
    identity = "https://example.com/python-packaging"
    clusters_file.write_text(
        json.dumps(
            {
                "schema_version": common_module.TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
                "cluster_count": 1,
                "clusters": [
                    {
                        "cluster_id": "bc_python",
                        "bookmark_identities": [identity],
                        "representative_bookmarks": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    response_file.write_text(
        """```json
{
  "schema_version": "user_taxonomy_response/v1",
  "root_groups": [{"name": "主要主题", "roots": ["学习"]}],
  "categories": [
    {
      "path": "学习/Python",
      "description": "Python 学习资料",
      "aliases": ["python", "packaging"],
      "title_patterns": ["[Pp]ython"],
      "topic_domains": ["python.org"]
    }
  ],
  "cluster_assignments": [{"cluster_id": "bc_python", "category": "学习/Python", "confidence": "high"}],
  "uncategorized_cluster_ids": [],
  "notes": []
}
```""",
        encoding="utf-8",
    )
    config_file.write_text(
        json.dumps(
            {
                "input": {
                    "user_taxonomy_file": str(taxonomy_file),
                    "bookmark_assignment_file": str(assignments_file),
                },
                "output": {
                    "reports_directory": str(tmp_path / "reports"),
                    "taxonomy_bootstrap_clusters_file": str(clusters_file),
                },
                "logging": {"file": str(tmp_path / "app.log"), "console": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["python3", "scripts/apply_taxonomy_response.py", "--config", str(config_file), "--response", str(response_file)],
        cwd=ROOT,
        check=True,
    )

    taxonomy = json.loads(taxonomy_file.read_text(encoding="utf-8"))
    assignments = json.loads(assignments_file.read_text(encoding="utf-8"))
    assert taxonomy["schema_version"] == common_module.USER_TAXONOMY_SCHEMA_VERSION
    assert "学习/Python" in taxonomy["categories"]
    assert assignments["assignments"][identity]["category"] == "学习/Python"

    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file, assignment_file=assignments_file)
    classification = classifier.classify_bookmark(
        {
            "id": "bookmark_python",
            "name": "Python Packaging Guide",
            "url": identity,
            "domain": "example.com",
            "original_folder_path": [],
            "metadata": build_metadata("Python Packaging Guide", "", "python,packaging", "Example", "python packaging docs"),
        }
    )
    assert classification["category"] == "学习/Python"
    assert classification["confidence_components"]["llm_assignment_applied"] is True
    assert classification["top_decision_drivers"][0]["driver"] == "llm_cluster_assignment"


def test_apply_taxonomy_response_merge_existing_preserves_root_groups_and_upgrades_assignments(tmp_path):
    candidates_file = tmp_path / "taxonomy_followup_candidates.json"
    response_file = tmp_path / "taxonomy_followup_response.md"
    taxonomy_file = tmp_path / "generated" / "user_taxonomy.json"
    assignments_file = tmp_path / "generated" / "bookmark_taxonomy_assignments.json"
    config_file = tmp_path / "skill_config.json"

    candidates_file.write_text(
        json.dumps(
            {
                "schema_version": common_module.TAXONOMY_FOLLOWUP_CANDIDATES_SCHEMA_VERSION,
                "bundle_count": 1,
                "bundles": [
                    {
                        "bundle_id": "tf_docker",
                        "cluster_id": "tf_docker",
                        "bookmark_identities": [
                            "https://example.com/docker-guide",
                            "https://example.com/postgres-old",
                        ],
                        "representative_bookmarks": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    taxonomy_file.parent.mkdir(parents=True, exist_ok=True)
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": common_module.USER_TAXONOMY_SCHEMA_VERSION,
                "root_groups": [{"name": "主要主题", "roots": ["数据库"]}],
                "categories": {
                    "数据库/PostgreSQL": {
                        "domains": ["postgresql.org"],
                        "keywords": ["postgresql"],
                        "title_patterns": ["[Pp]ostgreSQL"],
                        "folder_keywords": [],
                        "description": "PostgreSQL 资料",
                        "source": "seed",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assignments_file.write_text(
        json.dumps(
            {
                "schema_version": common_module.BOOKMARK_TAXONOMY_ASSIGNMENTS_SCHEMA_VERSION,
                "assignments": {
                    "https://example.com/postgres-old": {
                        "category": "数据库/PostgreSQL",
                        "confidence": 0.4,
                        "confidence_label": "low",
                        "source_cluster_id": "seed_low",
                        "source": "seed",
                    },
                    "https://example.com/existing-keep": {
                        "category": "数据库/PostgreSQL",
                        "confidence": 0.95,
                        "confidence_label": "high",
                        "source_cluster_id": "seed_high",
                        "source": "seed",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    response_file.write_text(
        """```json
{
  "schema_version": "user_taxonomy_response/v1",
  "categories": [
    {
      "path": "容器/Docker",
      "description": "Docker 资料",
      "aliases": ["docker"],
      "title_patterns": ["Docker"],
      "topic_domains": ["docker.com"]
    }
  ],
  "cluster_assignments": [{"cluster_id": "tf_docker", "category": "容器/Docker", "confidence": "high"}],
  "uncategorized_cluster_ids": [],
  "notes": []
}
```""",
        encoding="utf-8",
    )
    config_file.write_text(
        json.dumps(
            {
                "input": {
                    "user_taxonomy_file": str(taxonomy_file),
                    "bookmark_assignment_file": str(assignments_file),
                },
                "output": {"reports_directory": str(tmp_path / "reports")},
                "logging": {"file": str(tmp_path / "app.log"), "console": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            "python3",
            "scripts/apply_taxonomy_response.py",
            "--config",
            str(config_file),
            "--response",
            str(response_file),
            "--clusters",
            str(candidates_file),
            "--merge-existing",
        ],
        cwd=ROOT,
        check=True,
    )

    taxonomy = json.loads(taxonomy_file.read_text(encoding="utf-8"))
    assignments = json.loads(assignments_file.read_text(encoding="utf-8"))
    assert taxonomy["root_groups"] == [{"name": "主要主题", "roots": ["数据库"]}]
    assert set(taxonomy["categories"]) == {"数据库/PostgreSQL", "容器/Docker"}
    assert assignments["assignments"]["https://example.com/docker-guide"]["category"] == "容器/Docker"
    assert assignments["assignments"]["https://example.com/postgres-old"]["category"] == "容器/Docker"
    assert assignments["assignments"]["https://example.com/postgres-old"]["confidence"] == 0.95
    assert assignments["assignments"]["https://example.com/existing-keep"]["category"] == "数据库/PostgreSQL"


def test_apply_taxonomy_response_rejects_generic_platform_topic_domain(tmp_path):
    clusters_payload = {"schema_version": common_module.TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION, "clusters": []}
    clusters_file = tmp_path / "clusters.json"
    clusters_file.write_text(json.dumps(clusters_payload), encoding="utf-8")
    response_file = tmp_path / "response.json"
    response_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy_response/v1",
                "root_groups": [],
                "categories": [{"path": "代码/GitHub", "topic_domains": ["github.com"], "aliases": [], "title_patterns": []}],
                "cluster_assignments": [],
                "uncategorized_cluster_ids": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        response = apply_taxonomy_module.load_response_payload(response_file)
        clusters = common_module.require_payload_schema(
            json.loads(clusters_file.read_text(encoding="utf-8")),
            common_module.TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
            "clusters",
            clusters_file,
        )
        category_rules, category_paths = apply_taxonomy_module.normalize_categories(response["categories"])
        apply_taxonomy_module.build_assignments(response, clusters, category_paths)
    except ValueError as exc:
        assert "通用平台" in str(exc)
    else:
        raise AssertionError("expected generic platform topic domain to be rejected")


def test_apply_taxonomy_response_escapes_literal_title_patterns():
    categories, _ = apply_taxonomy_module.normalize_categories(
        [
            {
                "path": "编程语言/C++",
                "aliases": [],
                "title_patterns": ["C++", "TLA+", "Spider 2.0", "Go"],
                "topic_domains": [],
            }
        ]
    )
    patterns = categories["编程语言/C++"]["title_patterns"]

    assert any(re.search(pattern, "Modern C++ Template Guide", re.IGNORECASE) for pattern in patterns)
    assert any(re.search(pattern, "TLA+ video course", re.IGNORECASE) for pattern in patterns)
    assert any(re.search(pattern, "Spider 2.0 benchmark", re.IGNORECASE) for pattern in patterns)
    assert any(re.search(pattern, "Go runtime notes", re.IGNORECASE) for pattern in patterns)
    assert not any(re.search(pattern, "Redistributing Tables", re.IGNORECASE) for pattern in patterns)
    assert not any(re.search(pattern, "MongoDB guide", re.IGNORECASE) for pattern in patterns)


def test_broken_links_report_export(tmp_path):
    bookmarks = [
        {
            "id": "bookmark_1",
            "name": "Broken",
            "url": "https://example.com/missing",
            "metadata": {"fetch_status": "broken", "status_code": 404, "error": "HTTP 404"},
        }
    ]
    report = tmp_path / "broken_links.json"
    count = fetch_module.export_broken_links_report(bookmarks, report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    assert count == 1
    assert exported["broken_links"][0]["status_code"] == 404


def test_fetch_hotspots_report_export_summarizes_domains_and_pass_deltas(tmp_path):
    primary = [
        {
            "id": "bookmark_proxy_timeout",
            "name": "Zhihu timeout",
            "url": "https://zhuanlan.zhihu.com/p/1",
            "domain": "zhuanlan.zhihu.com",
            "metadata": {"fetch_status": "timeout", "error": "Request timeout", "fetch_context": {"route": "proxy"}},
        },
        {
            "id": "bookmark_csdn_proxy",
            "name": "CSDN proxy error",
            "url": "https://blog.csdn.net/post-1",
            "domain": "blog.csdn.net",
            "metadata": {"fetch_status": "error", "error": "Connection reset", "fetch_context": {"route": "proxy"}},
        },
    ]
    final = [
        {
            "id": "bookmark_zhihu_direct_ok",
            "name": "Zhihu success",
            "url": "https://zhuanlan.zhihu.com/p/1",
            "domain": "zhuanlan.zhihu.com",
            "metadata": {"fetch_status": "success", "status_code": 200, "fetch_context": {"route": "direct"}},
        },
        {
            "id": "bookmark_csdn_direct_error",
            "name": "CSDN direct error",
            "url": "https://blog.csdn.net/post-1",
            "domain": "blog.csdn.net",
            "metadata": {"fetch_status": "error", "error": "Connection reset", "fetch_context": {"route": "direct"}},
        },
        {
            "id": "bookmark_csdn_http",
            "name": "CSDN 403",
            "url": "https://blog.csdn.net/post-2",
            "domain": "blog.csdn.net",
            "metadata": {"fetch_status": "broken", "status_code": 403, "error": "HTTP 403", "fetch_context": {"route": "proxy"}},
        },
    ]

    report = tmp_path / "fetch_hotspots.json"
    count = fetch_module.export_fetch_hotspots_report(primary, final, report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    domains = {item["domain"]: item for item in exported["domains"]}

    assert count == 2
    assert exported["schema_version"] == common_module.FETCH_HOTSPOTS_SCHEMA_VERSION
    assert domains["zhuanlan.zhihu.com"]["review_count"] == 0
    assert domains["zhuanlan.zhihu.com"]["pass_deltas"] == {"review_delta": -1, "success_delta": 1}
    assert domains["blog.csdn.net"]["review_count"] == 2
    assert {row["reason_code"] for row in domains["blog.csdn.net"]["reason_codes"]} == {"http_error", "other_error"}
    assert {row["route"] for row in domains["blog.csdn.net"]["routes"]} == {"direct", "proxy"}


class FakeResponse:
    def __init__(self, status: int, url: str, html: str):
        self.status = status
        self.url = url
        self._html = html

    async def text(self, errors: str = "ignore") -> str:
        return self._html


class FakeRequestContext:
    def __init__(self, response: FakeResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, pages: dict[str, FakeResponse]):
        self.pages = pages
        self.requested_urls: list[str] = []

    def get(self, url: str, **kwargs):
        self.requested_urls.append(url)
        return FakeRequestContext(self.pages[url])


def test_fetch_with_site_profile_and_homepage_enrichment():
    deep_url = "https://docs.example.com/manual/api/ref?lang=en&mode=full"
    homepage_url = "https://docs.example.com/"
    deep_html = """
    <html lang='en'><head>
      <title>Docs</title>
      <meta name='description' content='Quick reference'>
      <meta property='og:title' content='API Reference'>
      <meta property='og:description' content='Reference manual'>
      <meta property='og:site_name' content='Example Docs'>
      <meta name='twitter:title' content='API Reference on X'>
      <meta name='twitter:description' content='Reference on X'>
      <link rel='canonical' href='/canonical/ref'>
      <script type='application/ld+json'>{"@type":"TechArticle"}</script>
    </head><body>
      <nav>Docs API Guides</nav>
      <main><h1>Reference</h1><h2>Authentication</h2>Short docs page.</main>
    </body></html>
    """
    homepage_html = """
    <html lang='en'><head>
      <title>Example Docs Home</title>
      <meta property='og:site_name' content='Example Docs'>
      <meta property='og:description' content='Developer documentation portal'>
    </head><body>
      <main><h1>Example Docs</h1><h2>Guides</h2>Documentation manual reference api sdk portal.</main>
    </body></html>
    """
    session = FakeSession({
        deep_url: FakeResponse(200, deep_url, deep_html),
        homepage_url: FakeResponse(200, homepage_url, homepage_html),
    })

    metadata = fetch_module.asyncio.run(
        fetch_module.fetch_with_aiohttp(
            session,
            deep_url,
            timeout=3,
            max_retries=0,
            proxy_options={"enabled": False, "trust_env": False, "http_proxy": None, "https_proxy": None, "all_proxy": None},
        )
    )

    assert metadata["fetch_status"] == "success"
    assert metadata["page_signals"]["canonical_url"] == "https://docs.example.com/canonical/ref"
    assert metadata["page_signals"]["og:site_name"] == "Example Docs"
    assert metadata["page_signals"]["schema_types"] == ["TechArticle"]
    assert metadata["site_signals"]["homepage_fetch_status"] == "success"
    assert metadata["site_signals"]["site_name"] == "Example Docs"
    assert "documentation" in metadata["site_signals"]["site_type_candidates"]
    assert metadata["normalized_url"] == deep_url
    assert metadata["query_keys"] == ["lang", "mode"]
    assert metadata["path_segments"] == ["manual", "api", "ref"]
    assert metadata["site_profile"]["url"]["registrable_domain"] == "example.com"
    assert session.requested_urls == [deep_url, homepage_url]


def test_fetch_with_domain_override_can_skip_homepage_and_force_direct_route():
    deep_url = "https://docs.example.com/manual/api/ref"
    homepage_url = "https://docs.example.com/"
    deep_html = """
    <html lang='en'><head>
      <title>Ref</title>
      <meta property='og:title' content='API Reference'>
      <meta property='og:site_name' content='Example Docs'>
    </head><body><main><h1>Reference</h1>Short docs page.</main></body></html>
    """
    session = FakeSession({
        deep_url: FakeResponse(200, deep_url, deep_html),
        homepage_url: FakeResponse(200, homepage_url, "<html><body>unused</body></html>"),
    })

    metadata = fetch_module.asyncio.run(
        fetch_module.fetch_with_aiohttp(
            session,
            deep_url,
            timeout=3,
            max_retries=0,
            proxy_options={"enabled": True, "trust_env": True, "http_proxy": None, "https_proxy": None, "all_proxy": None},
            domain_overrides={
                "example.com": {"fetch_homepage": True},
                "docs.example.com": {"fetch_homepage": False, "prefer_direct": True},
            },
        )
    )

    assert metadata["fetch_status"] == "success"
    assert metadata["site_signals"]["homepage_fetch_status"] == "skipped"
    assert metadata["fetch_context"]["route"] == "direct"
    assert metadata["fetch_context"]["domain_override"] == "docs.example.com"
    assert session.requested_urls == [deep_url]


def test_fetch_helpers_identify_url_and_site_types():
    url_signals = fetch_module.extract_url_signals("https://blog.example.co.uk/post/1?tag=python&lang=en")
    assert url_signals["subdomain"] == "blog"
    assert url_signals["registrable_domain"] == "example.co.uk"
    assert url_signals["path_segments"] == ["post", "1"]
    assert url_signals["query_keys"] == ["tag", "lang"]

    docs_hints = fetch_module.infer_page_type_hints(["API docs manual reference"], ["guide", "api"])
    blog_hints = fetch_module.infer_page_type_hints(["Latest blog post article"], ["post"])
    product_hints = fetch_module.infer_page_type_hints(["Pricing features about our platform"], ["pricing"])
    assert "documentation" in docs_hints
    assert "blog" in blog_hints
    assert "product" in product_hints


def test_cli_respects_config_and_creates_outputs(tmp_path):
    sample = ROOT / "tests" / "fixtures" / "sample_bookmarks.html"
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "skill_config.json"
    cfg.write_text(
        json.dumps(
            {
                "input": {"bookmark_file": "../fixtures/sample_bookmarks.html"},
                "pipeline": {
                    "copied_bookmark_file": "../runtime/data/bookmarks.html",
                    "parsed_file": "../runtime/data/parsed.json",
                    "classified_file": "../runtime/data/classified.json",
                    "clustering_file": "../runtime/data/clustered.json"
                },
                "output": {
                    "html_file": "../runtime/output/organized.html",
                    "reports_directory": "../runtime/output/reports",
                    "duplicate_report_file": "../runtime/output/reports/duplicates.json",
                    "broken_links_report_file": "../runtime/output/reports/broken_links.json",
                    "confirmation_report_file": "../runtime/output/reports/needs_confirmation.json"
                },
                "fetch_options": {
                    "concurrent_limit": 1,
                    "timeout": 1,
                    "delay": 0,
                    "batch_size": 10,
                    "max_retries": 0,
                    "cache_file": "../runtime/data/enriched.json",
                    "user_agent": "pytest"
                },
                "classification_options": {"confirm_threshold": 90, "title_weight": 20},
                "clustering_options": {"min_cluster_size": 2, "max_bookmarks_without_clustering": 3},
                "logging": {"level": "INFO", "file": "../runtime/logs/app.log", "console": False}
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    fixture_copy = fixtures_dir / "sample_bookmarks.html"
    fixture_copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

    subprocess.run(["python3", "scripts/1_copy_bookmark.py", "--config", str(cfg)], cwd=ROOT, check=True)
    subprocess.run(["python3", "scripts/2_parse_bookmarks.py", "--config", str(cfg)], cwd=ROOT, check=True)
    write_enriched_fixture(tmp_path / "runtime" / "data" / "parsed.json", tmp_path / "runtime" / "data" / "enriched.json")
    subprocess.run(["python3", "scripts/4_classify_bookmarks.py", "--config", str(cfg)], cwd=ROOT, check=True)
    subprocess.run(["python3", "scripts/5_cluster_bookmarks.py", "--config", str(cfg)], cwd=ROOT, check=True)
    subprocess.run(["python3", "scripts/6_generate_html.py", "--config", str(cfg)], cwd=ROOT, check=True)

    assert (tmp_path / "runtime" / "output" / "organized.html").exists()
    assert (tmp_path / "runtime" / "output" / "reports" / "needs_confirmation.json").exists()
    assert (tmp_path / "runtime" / "output" / "reports" / "duplicates.json").exists()
    assert (tmp_path / "runtime" / "output" / "reports" / "rule_suggestions.json").exists()
    assert (tmp_path / "runtime" / "output" / "reports" / "quality_report.json").exists()
    assert (tmp_path / "runtime" / "output" / "reports" / "signal_audit.json").exists()
    assert (tmp_path / "runtime" / "logs" / "app.log").exists()


def test_classifier_keeps_generated_taxonomy_and_open_topics_together(tmp_path):
    taxonomy_file = tmp_path / "user_taxonomy.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy/v1",
                "categories": {
                    "数据库/PostgreSQL": {
                        "domains": [],
                        "keywords": ["postgresql", "postgres"],
                        "title_patterns": ["[Pp]ostgreSQL", "[Pp]ostgres"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file)
    bookmark = {
        "id": "bookmark_mix",
        "name": "PostgreSQL + Neon branch workflow",
        "url": "https://neon.tech/docs/guides/branching",
        "domain": "neon.tech",
        "original_folder_path": ["数据库"],
        "metadata": {
            "title": "PostgreSQL branching on Neon",
            "description": "Serverless Postgres branching tutorial",
            "site_profile": "Neon provides serverless PostgreSQL with branching",
            "keywords": "postgresql, branching, neon",
        },
    }
    classification = classifier.classify_bookmark(bookmark)
    assert "数据库/PostgreSQL" in classification["primary_topics"]
    assert any(candidate["topic"] == "Neon" for candidate in classification["open_topic_candidates"])


def test_classifier_uses_generated_taxonomy_without_rule_files(tmp_path):
    taxonomy_file = tmp_path / "user_taxonomy.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy/v1",
                "categories": {
                    "编程语言/Python": {
                        "domains": ["neon.tech"],
                        "keywords": ["python", "packaging"],
                        "title_patterns": ["[Pp]ython"],
                    },
                    "编程语言/Go": {
                        "domains": ["go.dev"],
                        "keywords": ["golang"],
                        "title_patterns": ["[Gg]olang"],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file)
    domain_hit = classifier.classify_bookmark(
        {
            "id": "bookmark_override_domain",
            "name": "Neon Quickstart",
            "url": "https://neon.tech/docs/quickstart",
            "domain": "neon.tech",
            "original_folder_path": ["数据库"],
            "metadata": build_metadata("Neon Quickstart", "", "", "Neon", ""),
        }
    )
    keyword_hit = classifier.classify_bookmark(
        {
            "id": "bookmark_base_keyword",
            "name": "Python packaging notes",
            "url": "https://example.com/python-packaging",
            "domain": "example.com",
            "original_folder_path": ["学习"],
            "metadata": build_metadata("Python packaging notes", "", "python,packaging", "Example", "packaging docs"),
        }
    )

    assert domain_hit["category"] == "编程语言/Python"
    assert any(item["signal"] == "domain" for item in domain_hit["classification_evidence"]["topic_scores"][0]["evidence"])
    assert keyword_hit["category"] == "编程语言/Python"
    assert any(item["signal"] == "keywords" for item in keyword_hit["classification_evidence"]["topic_scores"][0]["evidence"])
    go_hit = classifier.classify_bookmark(
        {
            "id": "bookmark_topic_override",
            "name": "Golang release notes",
            "url": "https://go.dev/doc/devel/release",
            "domain": "go.dev",
            "original_folder_path": ["学习"],
            "metadata": build_metadata("Golang release notes", "", "golang", "Go", "golang docs"),
        }
    )
    python_still_hit = classifier.classify_bookmark(
        {
            "id": "bookmark_base_still_present",
            "name": "Python packaging notes",
            "url": "https://example.com/python-packaging",
            "domain": "example.com",
            "original_folder_path": ["学习"],
            "metadata": build_metadata("Python packaging notes", "", "python", "Example", "python docs"),
        }
    )
    assert go_hit["category"] == "编程语言/Go"
    assert python_still_hit["category"] == "编程语言/Python"



def test_classifier_distinguishes_resource_types_within_same_topic():
    classifier = classify_module.BookmarkClassifier()
    official_doc = {
        "id": "bookmark_doc",
        "name": "Kubernetes Documentation",
        "url": "https://kubernetes.io/docs/concepts/overview/",
        "domain": "kubernetes.io",
        "original_folder_path": ["运维"],
        "metadata": {
            "title": "Kubernetes Documentation",
            "description": "Official Kubernetes docs",
            "site_profile": "Official production-grade container orchestration",
        },
    }
    community_blog = {
        "id": "bookmark_blog",
        "name": "Kubernetes incident notes",
        "url": "https://medium.com/@ops/kubernetes-incident-notes-123",
        "domain": "medium.com",
        "original_folder_path": ["运维"],
        "metadata": {
            "title": "Kubernetes incident notes",
            "description": "A blog post about debugging Kubernetes networking",
            "site_profile": "Community blog post for SREs",
        },
    }
    doc_classification = classifier.classify_bookmark(official_doc)
    blog_classification = classifier.classify_bookmark(community_blog)
    assert doc_classification["category"] == "待整理"
    assert blog_classification["category"] == "待整理"
    assert doc_classification["primary_topics"] == []
    assert blog_classification["primary_topics"] == []
    assert doc_classification["resource_type"] == "文档"
    assert blog_classification["resource_type"] == "博客"
    assert "官方" in doc_classification["quality_signals"]
    assert "社区" in blog_classification["quality_signals"]


def test_classifier_repository_resource_type_requires_repository_path():
    classifier = classify_module.BookmarkClassifier()
    repo_page = {
        "id": "bookmark_repo_page",
        "name": "GitHub - example/postgres-tool",
        "url": "https://github.com/example/postgres-tool",
        "domain": "github.com",
        "original_folder_path": ["代码"],
        "metadata": {
            "title": "GitHub - example/postgres-tool: PostgreSQL backup utility",
            "description": "PostgreSQL backup utility repository",
            "keywords": "postgresql,backup,repository",
        },
    }
    marketing_page = {
        "id": "bookmark_marketing_page",
        "name": "GitHub Actions",
        "url": "https://github.com/features/actions",
        "domain": "github.com",
        "original_folder_path": ["代码"],
        "metadata": {
            "title": "GitHub Actions",
            "description": "Automate your workflow from idea to production",
            "keywords": "automation,ci,cd",
        },
    }

    repo_classification = classifier.classify_bookmark(repo_page)
    marketing_classification = classifier.classify_bookmark(marketing_page)

    assert repo_classification["resource_type"] == "仓库"
    assert marketing_classification["resource_type"] != "仓库"


def test_clusterer_prefers_classification_resource_type_when_metadata_lacks_it():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmark = {
        "id": "bookmark_type_gap",
        "name": "Alignment Notes",
        "url": "https://example.com/alignment-notes",
        "domain": "example.com",
        "original_folder_path": ["Research"],
        "metadata": {
            "title": "Alignment Notes",
            "description": "Interpretability and evaluation notes",
            "content_preview": "Interpretability and evaluation notes",
        },
        "classification": {
            "category": "机器学习/AI",
            "resource_type": "论文",
            "all_scores": {"机器学习/AI": {"total": 88}},
        },
    }

    feature = clusterer.build_feature_set(bookmark)

    assert feature.resource_type == "论文"
    assert "论文" in feature.resource_types


def test_classifier_exports_open_topic_only_bookmarks_for_confirmation(tmp_path):
    classifier = classify_module.BookmarkClassifier(classification_options={"confirm_threshold": 80})
    bookmark = {
        "id": "bookmark_open_topic",
        "name": "AcmeFlow release notes",
        "url": "https://acmeflow.dev/releases/v1-2",
        "domain": "acmeflow.dev",
        "original_folder_path": ["Inbox"],
        "metadata": {
            "title": "AcmeFlow 1.2 release notes",
            "description": "Release notes for the AcmeFlow developer workflow platform",
            "site_profile": "AcmeFlow developer workflow platform release notes",
            "keywords": "acmeflow, workflow, release notes",
        },
    }

    results, stats, confirm_needed = classifier.classify_all([bookmark])
    classification = results[0]["classification"]

    assert classification["category"] == classifier.default_category
    assert classification["primary_topics"] == []
    assert classification["open_topic_candidates"]
    assert any(candidate["topic"].lower() == "acmeflow" for candidate in classification["open_topic_candidates"])
    assert stats["confirm_needed_count"] == 1
    assert stats["confirm_needed_ids"] == ["bookmark_open_topic"]
    assert confirm_needed[0]["id"] == "bookmark_open_topic"

    report = tmp_path / "needs_confirmation.json"
    classify_module.export_confirmation_report(confirm_needed, report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    assert exported["count"] == 1
    assert exported["bookmarks"][0]["id"] == "bookmark_open_topic"
    assert exported["bookmarks"][0]["primary_topics"] == []
    assert exported["bookmarks"][0]["confirmation_bucket"] == "rule_gap"
    assert "rule_coverage_gap_on_successful_fetch" in exported["bookmarks"][0]["needs_confirmation_reasons"]


def test_classifier_treats_folder_as_weak_prior_and_reports_low_confidence():
    classifier = classify_module.BookmarkClassifier(classification_options={"confirm_threshold": 80})
    bookmark = {
        "id": "bookmark_folder_bias",
        "name": "Kubernetes security hardening checklist",
        "url": "https://owasp.org/www-project-kubernetes-top-ten/",
        "domain": "owasp.org",
        "original_folder_path": ["Python 学习"],
        "metadata": {
            "title": "Kubernetes Security Hardening Checklist",
            "description": "Security checklist for production Kubernetes clusters",
            "site_profile": "OWASP project for Kubernetes security",
        },
    }
    results, stats, _ = classifier.classify_all([bookmark])
    classification = results[0]["classification"]
    assert classification["category"] == "待整理"
    assert classification["primary_topics"] == []
    assert classification["classification_evidence"]["topic_scores"] == []
    assert any(candidate["topic"].lower() == "kubernetes" for candidate in classification["open_topic_candidates"])
    assert classification["folder_alignment_score"] == 0
    assert stats["low_confidence_normal_category_count"] == 0
    assert "resource_type_distribution" in stats
    assert "uncovered_topic_candidates" in stats


def test_classifier_ignores_stale_chrome_folder_for_topic_assignment():
    classifier = classify_module.BookmarkClassifier()
    chrome_extension = {
        "id": "bookmark_chrome_extension",
        "name": "Chrome 扩展程序 | Chrome Extensions | Chrome for Developers",
        "url": "https://developer.chrome.com/docs/extensions",
        "domain": "developer.chrome.com",
        "original_folder_path": ["数据库", "PostgreSQL", "TiDB"],
        "metadata": {
            "title": "Chrome 扩展程序 | Chrome Extensions | Chrome for Developers",
            "description": "了解如何开发 Chrome 扩展程序。",
            "keywords": "chrome extensions web",
            "site_profile": {
                "site": {"site_name": "Chrome for Developers", "brand_terms": ["Chrome", "Extensions"]},
                "page": {"page_type_hints": ["documentation"]},
            },
        },
    }
    stackoverflow_go = {
        "id": "bookmark_stackoverflow_go",
        "name": "How to Return Nil String in Go? - Stack Overflow",
        "url": "https://stackoverflow.com/questions/52255683/how-to-return-nil-string-in-go",
        "domain": "stackoverflow.com",
        "original_folder_path": ["数据库", "TiDB"],
        "metadata": {},
    }

    chrome_classification = classifier.classify_bookmark(chrome_extension)
    go_classification = classifier.classify_bookmark(stackoverflow_go)

    assert chrome_classification["category"] == "待整理"
    assert "数据库/TiDB" not in chrome_classification["primary_topics"]
    assert go_classification["category"] == "待整理"
    assert "数据库/TiDB" not in go_classification["primary_topics"]
    assert go_classification["folder_alignment_score"] == 0.0


def test_classifier_suppresses_generic_platform_open_topics():
    classifier = classify_module.BookmarkClassifier()
    bookmark = {
        "id": "bookmark_generic_platform",
        "name": "GitHub - example/postgres-tool: PostgreSQL backup utility",
        "url": "https://github.com/example/postgres-tool",
        "domain": "github.com",
        "original_folder_path": ["数据库"],
        "metadata": {
            "title": "GitHub - example/postgres-tool",
            "description": "PostgreSQL backup utility repository",
            "keywords": "postgresql, backup, repository",
            "site_profile": {
                "site": {"site_name": "GitHub", "brand_terms": ["GitHub"]},
                "page": {"og:title": "GitHub - example/postgres-tool"},
            },
        },
    }

    classification = classifier.classify_bookmark(bookmark)

    open_topics = {candidate["topic"].lower() for candidate in classification["open_topic_candidates"]}
    assert "github" not in open_topics
    assert "repository" not in open_topics
    assert all("github" not in hint.lower() for hint in classification["cluster_hints"])


def test_classifier_suppresses_source_like_generic_doc_tokens():
    classifier = classify_module.BookmarkClassifier()
    bookmark = {
        "id": "bookmark_docs_qq",
        "name": "多机集群部署方式说明",
        "url": "https://docs.qq.com/doc/example",
        "domain": "docs.qq.com",
        "original_folder_path": ["资料"],
        "metadata": {
            "title": "多机集群部署方式说明",
            "description": "腾讯文档，支持多人在线编辑 Word、Excel 和 PPT 文档",
            "site_profile": {
                "site": {
                    "site_name": "腾讯文档",
                    "brand_terms": ["在线文档", "Excel", "Word"],
                },
                "page": {"og:title": "多机集群部署方式说明"},
            },
        },
    }

    classification = classifier.classify_bookmark(bookmark)

    open_topics = {candidate["topic"].lower() for candidate in classification["open_topic_candidates"]}
    assert "excel" not in open_topics
    assert "word" not in open_topics
    assert "腾讯文档" not in classification["cluster_hints"]


def test_classifier_generic_platform_title_suffix_does_not_become_topic():
    classifier = classify_module.BookmarkClassifier()
    bookmark = {
        "id": "bookmark_jianshu_suffix",
        "name": "Docker 网络排障 - 简书",
        "url": "https://www.jianshu.com/p/docker-networking",
        "domain": "www.jianshu.com",
        "original_folder_path": ["Inbox"],
        "metadata": {
            "title": "Docker 网络排障 - 简书",
            "description": "排查 Docker 网络故障的实战笔记",
            "keywords": "docker,networking,troubleshooting",
            "site_profile": {
                "site": {"site_name": "简书", "brand_terms": ["创作你的创作"]},
                "page": {"og:title": "Docker 网络排障 - 简书"},
            },
        },
    }

    classification = classifier.classify_bookmark(bookmark)

    open_topics = {candidate["topic"].lower() for candidate in classification["open_topic_candidates"]}
    assert "简书" not in classification["cluster_hints"]
    assert "创作你的创作" not in classification["cluster_hints"]
    assert "简书" not in open_topics
    assert "docker" in open_topics


def test_classifier_keeps_user_taxonomy_assignment_for_review_required_links(tmp_path):
    bookmark = {
        "id": "bookmark_untrusted_failure",
        "name": "TiDB Architecture Guide",
        "url": "https://book.tidb.io/session/architecture.html",
        "domain": "book.tidb.io",
        "original_folder_path": ["数据库", "TiDB"],
        "metadata": {
            "title": "TiDB Architecture Guide",
            "description": "TiDB distributed database architecture",
            "keywords": "tidb,tikv,pingcap",
            "fetch_status": "error",
            "link_health": {
                "review_required": True,
                "trusted_override": False,
                "reason_label": "DNS/连接失败",
                "reason_code": "dns_connection",
            },
        },
    }
    identity = common_module.build_signal_pack(bookmark)["identity"]["canonical_identity"]
    assignments_file = tmp_path / "assignments.json"
    assignments_file.write_text(
        json.dumps(
            {
                "schema_version": common_module.BOOKMARK_TAXONOMY_ASSIGNMENTS_SCHEMA_VERSION,
                "assignments": {
                    identity: {
                        "category": "数据库/TiDB",
                        "confidence": 0.95,
                        "source_cluster_id": "bc_test",
                        "source": "llm_cluster_assignment",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classifier = classify_module.BookmarkClassifier(assignment_file=assignments_file)

    classification = classifier.classify_bookmark(bookmark)

    assert classification["category"] == "数据库/TiDB"
    assert classification["review_required"] is True
    assert classification["needs_confirmation"] is True
    assert classification["confirmation_bucket"] == "fetch_blocked"
    assert classification["rule_confidence"] == 0.95
    assert "待审阅" in classification["quality_signals"]
    assert classification["classification_evidence"]["llm_assignment"]["category"] == "数据库/TiDB"
    assert classification["confidence_components"]["llm_assignment_applied"] is True


def test_classifier_keeps_strong_rule_topic_for_review_required_links(tmp_path):
    taxonomy_file = tmp_path / "user_taxonomy.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy/v1",
                "categories": {
                    "数据库/TiDB": {
                        "domains": ["book.tidb.io"],
                        "keywords": ["tidb", "tikv", "pingcap"],
                        "title_patterns": ["TiDB"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file)
    bookmark = {
        "id": "bookmark_review_keep",
        "name": "TiDB Architecture Guide",
        "url": "https://book.tidb.io/session/architecture.html",
        "domain": "book.tidb.io",
        "original_folder_path": ["数据库", "TiDB"],
        "metadata": {
            "title": "TiDB Architecture Guide",
            "description": "TiDB distributed database architecture",
            "keywords": "tidb,tikv,pingcap",
            "fetch_status": "error",
            "link_health": {
                "review_required": True,
                "trusted_override": False,
                "reason_label": "DNS/连接失败",
                "reason_code": "dns_connection",
            },
        },
    }

    classification = classifier.classify_bookmark(bookmark)

    assert classification["category"] == "数据库/TiDB"
    assert classification["review_required"] is True
    assert classification["needs_confirmation"] is True
    assert classification["confirmation_bucket"] == "fetch_blocked"
    assert classification["rule_confidence"] >= 0.55
    assert classification["confidence_components"]["review_topic_preserved"] is True
    assert "待审阅" in classification["quality_signals"]


def test_classifier_generated_taxonomy_rules_cover_product_specific_families(tmp_path):
    taxonomy_file = tmp_path / "user_taxonomy.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy/v1",
                "categories": {
                    "数据库/openGauss": {"domains": ["opengauss.org", "docs.opengauss.org"], "keywords": ["opengauss"], "title_patterns": ["openGauss"]},
                    "数据库/DuckDB": {"domains": ["duckdb.org"], "keywords": ["duckdb"], "title_patterns": ["DuckDB"]},
                    "数据库/BenchmarkSQL": {"domains": ["benchmarksql.readthedocs.io"], "keywords": ["benchmarksql", "tpc-c"], "title_patterns": ["BenchmarkSQL"]},
                    "数据库/InfluxDB": {"domains": ["influxdata.com", "docs.influxdata.com"], "keywords": ["influxdb", "line protocol"], "title_patterns": ["InfluxDB"]},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file)

    opengauss = classifier.classify_bookmark(
        {
            "id": "bookmark_opengauss",
            "name": "openGauss 文档中心",
            "url": "https://docs.opengauss.org/zh/docs/latest/docs/Developerguide/index.html",
            "domain": "docs.opengauss.org",
            "original_folder_path": ["数据库"],
            "metadata": build_metadata("openGauss 文档中心", "openGauss developer guide", "opengauss", "openGauss", "openGauss docs"),
        }
    )
    duckdb = classifier.classify_bookmark(
        {
            "id": "bookmark_duckdb",
            "name": "DuckDB Python API",
            "url": "https://duckdb.org/docs/stable/clients/python/overview.html",
            "domain": "duckdb.org",
            "original_folder_path": ["数据库"],
            "metadata": build_metadata("DuckDB Python API", "duckdb client docs", "duckdb,python", "DuckDB", "duckdb docs"),
        }
    )
    benchmarksql = classifier.classify_bookmark(
        {
            "id": "bookmark_benchmarksql",
            "name": "TPCC测试 ｜ BenchmarkSQL",
            "url": "https://benchmarksql.readthedocs.io/en/latest/",
            "domain": "benchmarksql.readthedocs.io",
            "original_folder_path": ["数据库"],
            "metadata": build_metadata("BenchmarkSQL Documentation", "TPC-C benchmark driver", "benchmarksql,tpc-c", "BenchmarkSQL", "benchmarksql docs"),
        }
    )
    influxdb = classifier.classify_bookmark(
        {
            "id": "bookmark_influxdb",
            "name": "InfluxDB line protocol reference",
            "url": "https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/",
            "domain": "docs.influxdata.com",
            "original_folder_path": ["数据库"],
            "metadata": build_metadata("InfluxDB line protocol reference", "influxdb docs", "influxdb,line protocol", "InfluxDB", "influxdb docs"),
        }
    )

    assert opengauss["category"] == "数据库/openGauss"
    assert duckdb["category"] == "数据库/DuckDB"
    assert benchmarksql["category"] == "数据库/BenchmarkSQL"
    assert influxdb["category"] == "数据库/InfluxDB"


def test_classifier_generated_taxonomy_rules_cover_operational_tool_families(tmp_path):
    taxonomy_file = tmp_path / "user_taxonomy.json"
    taxonomy_file.write_text(
        json.dumps(
            {
                "schema_version": "user_taxonomy/v1",
                "categories": {
                    "DevOps/Ansible": {"domains": ["ansible.com", "ansible.com.cn"], "keywords": ["ansible", "playbook"], "title_patterns": ["Ansible"]},
                    "开发工具/Chrome扩展": {"domains": ["developer.chrome.com"], "keywords": ["chrome extension", "manifest v3"], "title_patterns": ["Chrome Extensions", "Manifest V3"]},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classifier = classify_module.BookmarkClassifier(user_taxonomy_file=taxonomy_file)

    ansible = classifier.classify_bookmark(
        {
            "id": "bookmark_ansible",
            "name": "Ansible 中文权威指南",
            "url": "https://www.ansible.com.cn/",
            "domain": "www.ansible.com.cn",
            "original_folder_path": ["运维"],
            "metadata": build_metadata("Ansible 中文权威指南", "automation and playbooks", "ansible,playbook,inventory", "Ansible", "ansible docs"),
        }
    )
    chrome_extension = classifier.classify_bookmark(
        {
            "id": "bookmark_chrome_extension",
            "name": "迁移到 Manifest V3",
            "url": "https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3",
            "domain": "developer.chrome.com",
            "original_folder_path": ["开发"],
            "metadata": build_metadata("Chrome Extensions | Manifest V3", "chrome extension migration guide", "chrome extensions,manifest v3", "Chrome for Developers", "chrome extension docs"),
        }
    )

    assert ansible["category"] == "DevOps/Ansible"
    assert chrome_extension["category"] == "开发工具/Chrome扩展"


def test_classifier_keeps_general_reading_as_open_topic_without_personal_rules():
    classifier = classify_module.BookmarkClassifier()
    bookmark = {
        "id": "bookmark_gutenberg",
        "name": "Free eBooks | Project Gutenberg",
        "url": "https://m.gutenberg.org/",
        "domain": "m.gutenberg.org",
        "original_folder_path": ["数据库", "TiDB"],
        "metadata": {
            "title": "Free eBooks | Project Gutenberg",
            "description": "Project Gutenberg is a library of free eBooks.",
            "keywords": "books, ebooks, free, kindle",
            "site_profile": {
                "site": {"site_name": "Project Gutenberg", "brand_terms": ["Project", "Gutenberg", "eBooks"]},
                "page": {"h1": "Project Gutenberg is a library of over 75000 free eBooks"},
            },
        },
    }

    classification = classifier.classify_bookmark(bookmark)

    assert classification["category"] == "待整理"
    assert classification["rule_confidence"] == 0
    assert classification["primary_topics"] == []
    assert classification["open_topic_candidates"]


def test_rule_suggestions_do_not_bind_generic_platform_domains():
    report = cluster_module.generate_rule_suggestions(
        [
            {
                "cluster_id": "cluster_docs",
                "cluster_label": "腾讯文档",
                "rule_purity": 0.2,
                "destination_root": "发现主题",
                "dominant_categories": [{"category": "待整理", "root": "待整理", "count": 4, "share": 1.0}],
                "top_domains": [{"domain": "qq.com", "count": 4, "share": 1.0}],
                "representative_tokens": ["腾讯文档", "在线文档"],
                "discovered_topics": ["腾讯文档"],
                "bookmarks": [
                    {"id": f"bookmark_{index}", "name": f"Doc {index}", "url": f"https://docs.qq.com/doc/{index}", "domain": "docs.qq.com", "classification": {"category": "待整理"}}
                    for index in range(4)
                ],
            }
        ],
        discovery_root_name="发现主题",
        generic_platform_domains={"qq.com", "docs.qq.com"},
    )

    assert report["count"] == 1
    assert report["suggestions"][0]["proposed_domains"] == []
    assert report["suggestions"][0]["type"] == "create_topic"


def test_rule_suggestions_filter_public_suffix_and_invalid_domains():
    report = cluster_module.generate_rule_suggestions(
        [
            {
                "cluster_id": "cluster_domain_noise",
                "cluster_label": "RocksDB",
                "rule_purity": 0.72,
                "destination_root": "发现主题",
                "dominant_categories": [{"category": "数据库/RocksDB", "root": "数据库", "count": 4, "share": 1.0}],
                "top_domains": [
                    {"domain": "org.cn", "count": 2, "share": 0.5},
                    {"domain": "https_redis.io", "count": 1, "share": 0.25},
                    {"domain": "rocksdb.org", "count": 1, "share": 0.25},
                ],
                "representative_tokens": ["rocksdb"],
                "discovered_topics": ["RocksDB"],
                "bookmarks": [
                    {"id": f"bookmark_{index}", "name": f"RocksDB {index}", "url": f"https://rocksdb.org/{index}", "domain": "rocksdb.org", "classification": {"category": "数据库/RocksDB"}}
                    for index in range(4)
                ],
            }
        ],
        discovery_root_name="发现主题",
    )

    assert report["suggestions"][0]["type"] == "add_specific_domain"
    assert report["suggestions"][0]["proposed_domains"] == ["rocksdb.org"]


def test_quality_report_tracks_folder_and_generic_domain_metrics():
    bookmarks = [
        {
            "id": "bookmark_tidy",
            "name": "Untitled",
            "url": "https://docs.qq.com/doc/1",
            "domain": "docs.qq.com",
            "classification": {
                "category": "待整理",
                "needs_confirmation": True,
                "classification_evidence": {"topic_scores": []},
            },
        }
    ]
    suggestions = {
        "suggestions": [
            {
                "type": "create_topic",
                "proposed_domains": [],
            }
        ]
    }
    report = cluster_module.generate_quality_report(
        bookmarks,
        [
            {
                "cluster_id": "cluster_docs",
                "cluster_label": "腾讯文档",
                "destination_root": "待整理",
                "dominant_categories": [{"category": "待整理", "share": 1.0}],
                "representative_tokens": ["腾讯文档"],
                "bookmarks": bookmarks,
            }
        ],
        suggestions,
        display_hierarchy={
            "待整理": {
                "name": "待整理",
                "subcategories": {
                    "规则缺口": {
                        "name": "规则缺口",
                        "subcategories": {
                            "OpenAI": {"name": "OpenAI", "subcategories": {}, "bookmarks": bookmarks, "count": 1}
                        },
                        "bookmarks": [],
                        "count": 1,
                    }
                },
                "bookmarks": [],
                "count": 1,
            }
        },
        generic_platform_domains={"qq.com", "docs.qq.com"},
    )

    assert report["metrics"]["folder_only_classification_count"] == 0
    assert report["metrics"]["low_confidence_normal_category_count"] == 0
    assert report["metrics"]["generic_platform_domain_suggestion_count"] == 0
    assert report["metrics"]["tidy_cluster_count"] == 1
    assert report["metrics"]["flat_normal_root_count"] == 0
    assert report["metrics"]["display_top_level_root_count"] == 1
    assert report["metrics"]["tidy_visible_group_count"] == 1
    assert report["metrics"]["tidy_small_visible_group_count"] == 1
    assert report["metrics"]["review_required_count"] == 0
    assert report["metrics"]["review_required_normal_category_count"] == 0
    platform_report = cluster_module.generate_quality_report(
        bookmarks,
        [
            {
                "cluster_id": "cluster_github",
                "cluster_label": "GitHub",
                "destination_root": "发现主题",
                "dominant_categories": [
                    {"category": "数据库/TiDB", "share": 0.5},
                    {"category": "后端开发/Python Web", "share": 0.5},
                ],
                "top_domains": [{"domain": "github.com", "share": 1.0}],
                "representative_tokens": ["github"],
                "bookmarks": bookmarks * 5,
            }
        ],
        {"suggestions": []},
        generic_platform_domains={"github.com"},
    )
    assert platform_report["metrics"]["generic_platform_cluster_count"] == 1
    assert platform_report["metrics"]["largest_generic_platform_cluster_size"] == 5
    assert platform_report["largest_generic_platform_clusters"][0]["generic_platform_share"] == 1.0


def test_signal_audit_tracks_collection_and_usage():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmark = _bookmark(
        1,
        name="DuckDB Docs",
        url="https://duckdb.org/docs/stable/sql/introduction",
        domain="duckdb.org",
        category="数据库/DuckDB",
        folder=["数据库", "DuckDB"],
        description="duckdb sql documentation",
        keywords="duckdb,sql,docs",
    )
    bookmark["signal_pack"] = common_module.build_signal_pack(bookmark)
    bookmark["classification"] = {
        "category": "数据库/DuckDB",
        "rule_confidence": 0.91,
        "resource_type": "文档",
        "quality_signals": ["官方"],
        "used_signal_families": ["identity", "content", "structure"],
        "used_signal_fields": [
            "identity.domain",
            "content.title_candidates",
            "content.semantic_text",
            "structure.resource_facets",
        ],
        "cluster_hints": ["DuckDB"],
        "rule_roots": [{"root": "数据库", "support": 1.0, "total": 90.0}],
        "all_scores": {"数据库/DuckDB": {"total": 90.0}},
    }

    audit = cluster_module.generate_signal_audit([bookmark], clusterer)

    assert audit["schema_version"] == common_module.SIGNAL_AUDIT_SCHEMA_VERSION
    assert audit["summary"]["collected_field_count"] >= 10
    assert any(item["field"] == "identity.domain" for item in audit["fields"])
    assert any(item["family"] == "content" for item in audit["families"])


def test_require_payload_schema_rejects_stale_stage_outputs(tmp_path):
    stale_fetch = tmp_path / "stale_enriched.json"
    stale_fetch.write_text(json.dumps({"bookmarks": [], "stats": {}}, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        ["python3", "scripts/4_classify_bookmarks.py", "--input", str(stale_fetch), "--output", str(tmp_path / "classified.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "schema_version" in result.stdout


def test_compact_hierarchy_payload_keeps_html_generation_fields():
    payload = {
        "name": "数据库",
        "category": "数据库",
        "bookmarks": [
            {
                "id": "bookmark_1",
                "name": "DuckDB Docs",
                "url": "https://duckdb.org/docs",
                "domain": "duckdb.org",
                "metadata": {"title": "large payload"},
                "classification": {
                    "category": "数据库/DuckDB",
                    "resource_type": "文档",
                    "review_required": False,
                },
            }
        ],
        "subcategories": {},
        "count": 1,
    }

    compact = cluster_module.compact_hierarchy_payload(payload)

    assert compact["bookmarks"][0]["name"] == "DuckDB Docs"
    assert compact["bookmarks"][0]["classification"]["category"] == "数据库/DuckDB"
    assert "metadata" not in compact["bookmarks"][0]


def test_topic_collection_is_deterministic_for_same_inputs():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmark = _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/tutorial/", domain="fastapi.tiangolo.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api tutorial", keywords="python,fastapi,api")
    features = [clusterer.build_feature_set(bookmark) for _ in range(5)]
    topic_sets = [feature.topics for feature in features]
    assert all(topic_set == topic_sets[0] for topic_set in topic_sets[1:])
    assert features[0].primary_topic == "编程/Python Web"


def test_same_label_clusters_are_merged_instead_of_suffix_spam():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    clusterer._connected_components = lambda bookmarks, features, threshold=0.34: [bookmarks[:2], bookmarks[2:]]
    clusterer._split_if_needed = lambda clusters: clusters
    clusterer._merge_if_needed = lambda clusters: clusters
    bookmarks = [
        _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/tutorial/", domain="fastapi.tiangolo.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api tutorial", keywords="python,fastapi,api"),
        _bookmark(2, name="FastAPI 教程", url="https://realpython.com/fastapi-course/", domain="realpython.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="博客", description="python fastapi course", keywords="python,fastapi,course"),
        _bookmark(3, name="FastAPI 仓库模板", url="https://github.com/example/fastapi-template", domain="github.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="仓库", description="python fastapi template repo", keywords="python,fastapi,template"),
        _bookmark(4, name="FastAPI Worker 仓库", url="https://github.com/example/fastapi-worker", domain="github.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="仓库", description="python fastapi worker repo", keywords="python,fastapi,worker"),
    ]
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程/Python Web", threshold=1)
    names = sorted(hierarchy["subcategories"].keys())
    assert [name.lower() for name in names] == ["编程/python web/fastapi"]
    assert hierarchy["subcategories"][names[0]]["count"] == 4


def test_build_clustered_root_hierarchy_does_not_mutate_cluster_profiles():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="PostgreSQL Docs", url="https://postgresql.org/docs", domain="postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
        _bookmark(2, name="PostgreSQL Wiki", url="https://wiki.postgresql.org", domain="wiki.postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
    ]
    for bookmark in bookmarks:
        bookmark["classification"].update(
            {
                "display_category": "数据库/PostgreSQL",
                "resource_type": "文档",
                "rule_confidence": 0.93,
                "rule_roots": [{"root": "数据库", "support": 1.0, "total": 90.0}],
                "cluster_hints": ["PostgreSQL"],
            }
        )
    profiles = [
        {
            "cluster_id": "cluster_0001",
            "cluster_label": "PostgreSQL",
            "cluster_reason": "fixture",
            "destination_root": "数据库",
            "dominant_categories": [{"category": "数据库/PostgreSQL", "root": "数据库", "count": 1, "share": 1.0}],
            "dominant_rule_roots": [{"root": "数据库", "score": 1.0, "support": 1.0}],
            "discovered_topics": ["PostgreSQL"],
            "representative_tokens": ["postgresql"],
            "source_folder_reused": False,
            "source_folder_quality_score": 0.0,
            "size_below_threshold": True,
            "bookmarks": [bookmarks[0]],
        },
        {
            "cluster_id": "cluster_0002",
            "cluster_label": "PostgreSQL",
            "cluster_reason": "fixture",
            "destination_root": "数据库",
            "dominant_categories": [{"category": "数据库/PostgreSQL", "root": "数据库", "count": 1, "share": 1.0}],
            "dominant_rule_roots": [{"root": "数据库", "score": 1.0, "support": 1.0}],
            "discovered_topics": ["PostgreSQL"],
            "representative_tokens": ["postgresql"],
            "source_folder_reused": False,
            "source_folder_quality_score": 0.0,
            "size_below_threshold": True,
            "bookmarks": [bookmarks[1]],
        },
    ]
    before = [(profile["cluster_id"], [bookmark["id"] for bookmark in profile["bookmarks"]]) for profile in profiles]

    hierarchy = cluster_module.build_clustered_root_hierarchy(clusterer, profiles, threshold=20)

    after = [(profile["cluster_id"], [bookmark["id"] for bookmark in profile["bookmarks"]]) for profile in profiles]
    assert before == after
    assert hierarchy["数据库"]["subcategories"]["PostgreSQL"]["count"] == 2


def test_generic_repo_cluster_label_uses_repo_not_owner_or_broad_leaf():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2, generic_platform_domains={"github.com"})
    bookmark = _bookmark(
        1,
        name="Home · alibaba/tair Wiki",
        url="https://github.com/alibaba/tair/wiki",
        domain="github.com",
        category="机器学习/AI",
        folder=["代码"],
        resource_type="仓库",
        description="tair high performance cache storage project",
        keywords="tair,cache,storage",
    )
    bookmark["classification"].update(
        {
            "display_category": "机器学习/AI",
            "resource_type": "仓库",
            "rule_confidence": 0.91,
            "rule_roots": [{"root": "机器学习", "support": 1.0, "total": 90.0}],
            "cluster_hints": ["Alibaba", "Tair", "AI"],
        }
    )

    label = clusterer._cluster_display_name([bookmark], "机器学习/AI")

    assert label.lower() == "tair"
    assert "alibaba" not in label.lower()


def test_platform_slogan_is_not_used_as_generic_platform_cluster_label():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2, generic_platform_domains={"jianshu.com"})
    bookmarks = [
        _bookmark(
            index,
            name=f"PostgreSQL Executor {index} - 简书",
            url=f"https://www.jianshu.com/p/postgres-{index}",
            domain="www.jianshu.com",
            category="数据库/PostgreSQL",
            folder=["数据库"],
            description="创作你的创作 PostgreSQL executor internals",
            keywords="postgresql,executor",
        )
        for index in range(1, 3)
    ]
    for bookmark in bookmarks:
        bookmark["classification"].update(
            {
                "display_category": "数据库/PostgreSQL",
                "resource_type": "博客",
                "rule_confidence": 0.9,
                "rule_roots": [{"root": "数据库", "support": 1.0, "total": 90.0}],
                "cluster_hints": ["创作你的创作", "PostgreSQL"],
            }
        )

    label = clusterer._cluster_display_name(bookmarks, "数据库/PostgreSQL")
    tokens = clusterer._representative_tokens(bookmarks)

    assert label == "PostgreSQL"
    assert "创作你的创作" not in tokens


def test_generic_platform_small_mixed_cluster_splits_by_high_confidence_category():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=10, generic_platform_domains={"github.com"})
    bookmarks = [
        _bookmark(1, name="rocksdb compaction", url="https://github.com/facebook/rocksdb/wiki/Compaction", domain="github.com", category="数据库/RocksDB", folder=["代码"], resource_type="仓库", description="rocksdb compaction storage engine", keywords="rocksdb,compaction"),
        _bookmark(2, name="parquet cpp", url="https://github.com/apache/parquet-cpp", domain="github.com", category="编程语言/C-C++", folder=["代码"], resource_type="仓库", description="parquet cpp columnar format", keywords="cpp,parquet"),
        _bookmark(3, name="llm cache", url="https://github.com/alibaba/tair-kvcache", domain="github.com", category="机器学习/AI", folder=["代码"], resource_type="仓库", description="llm inference cache", keywords="llm,cache"),
    ]
    for bookmark in bookmarks:
        category = bookmark["classification"]["category"]
        bookmark["classification"].update(
            {
                "display_category": category,
                "resource_type": "仓库",
                "rule_confidence": 0.9,
                "rule_roots": [{"root": category.split("/")[0], "support": 1.0, "total": 90.0}],
                "cluster_hints": [category.split("/")[-1]],
            }
        )
    clusterer._connected_components = lambda bookmarks, features, threshold=0.34: [bookmarks]
    clusterer._split_if_needed = lambda clusters: clusters
    clusterer._merge_if_needed = lambda clusters: clusters

    clusters = clusterer.cluster_bookmarks(bookmarks)

    assert sorted(len(cluster) for cluster in clusters) == [1, 1, 1]
    assert len({cluster[0]["classification"]["category"] for cluster in clusters}) == 3


def test_build_root_hierarchy_preserves_leaf_categories_without_suffix_spam():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="Python 官方文档", url="https://docs.python.org/3/", domain="docs.python.org", category="编程语言/Python", folder=["学习", "Python"]),
        _bookmark(2, name="Rust 官方文档", url="https://doc.rust-lang.org/book/", domain="doc.rust-lang.org", category="编程语言/Rust", folder=["学习", "Rust"]),
        _bookmark(3, name="PostgreSQL Docs", url="https://postgresql.org/docs", domain="postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
        _bookmark(4, name="PostgreSQL Wiki", url="https://wiki.postgresql.org", domain="wiki.postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
    ]

    hierarchy = cluster_module.build_root_hierarchy(clusterer, bookmarks, threshold=20)

    programming = hierarchy["编程语言"]
    database = hierarchy["数据库"]
    assert programming["category"] == "编程语言"
    assert database["category"] == "数据库"
    assert programming["count"] == 2
    assert database["count"] == 2
    assert set(programming["subcategories"]) == {"Python", "Rust"}
    assert set(database["subcategories"]) == {"PostgreSQL"}
    assert len(programming["bookmarks"]) == 0
    assert len(database["bookmarks"]) == 0
    assert all(not name.endswith(")") for name in programming.get("subcategories", {}))
    assert all(not name.endswith(")") for name in database.get("subcategories", {}))


def test_build_root_hierarchy_keeps_small_pure_clusters_browsable():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="PostgreSQL Docs", url="https://postgresql.org/docs", domain="postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
        _bookmark(2, name="PostgreSQL Wiki", url="https://wiki.postgresql.org", domain="wiki.postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
    ]

    low_threshold = cluster_module.build_root_hierarchy(clusterer, bookmarks, threshold=1)
    high_threshold = cluster_module.build_root_hierarchy(clusterer, bookmarks, threshold=20)

    assert set(low_threshold["数据库"]["subcategories"]) == {"PostgreSQL"}
    assert set(high_threshold["数据库"]["subcategories"]) == {"PostgreSQL"}
    assert len(high_threshold["数据库"]["bookmarks"]) == 0


def test_build_display_hierarchy_groups_top_level_roots_for_human_browsing():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="Python 官方文档", url="https://docs.python.org/3/", domain="docs.python.org", category="编程语言/Python", folder=["学习", "Python"]),
        _bookmark(2, name="Rust 官方文档", url="https://doc.rust-lang.org/book/", domain="doc.rust-lang.org", category="编程语言/Rust", folder=["学习", "Rust"]),
        _bookmark(3, name="PostgreSQL Docs", url="https://postgresql.org/docs", domain="postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
        _bookmark(4, name="PostgreSQL Wiki", url="https://wiki.postgresql.org", domain="wiki.postgresql.org", category="数据库/PostgreSQL", folder=["学习", "PostgreSQL"]),
    ]

    root_hierarchy = cluster_module.build_root_hierarchy(clusterer, bookmarks, threshold=20)
    display_hierarchy = cluster_module.build_display_hierarchy(
        clusterer,
        root_hierarchy,
        common_module.DEFAULT_ROOT_GROUPS,
        common_module.DEFAULT_DISPLAY_OPTIONS,
    )

    assert list(display_hierarchy) == ["数据库", "编程语言"]
    assert "PostgreSQL" in display_hierarchy["数据库"]["subcategories"]
    assert "Python" in display_hierarchy["编程语言"]["subcategories"]

    html = html_module.BookmarkHTMLGenerator().generate_html(display_hierarchy)
    assert html.find("数据库") < html.find("编程语言")


def test_build_display_hierarchy_ignores_legacy_single_main_group_wrapper_in_auto_mode():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    root_hierarchy = {
        "数据库": {
            "name": "数据库",
            "category": "数据库",
            "bookmarks": [],
            "subcategories": {"PostgreSQL": {"name": "PostgreSQL", "category": "数据库/PostgreSQL", "bookmarks": [_bookmark(1, name="PG", url="https://postgresql.org/docs", domain="postgresql.org", category="数据库/PostgreSQL", folder=["数据库"])], "subcategories": {}, "count": 1}},
            "count": 1,
        },
        "编程语言": {
            "name": "编程语言",
            "category": "编程语言",
            "bookmarks": [],
            "subcategories": {"Python": {"name": "Python", "category": "编程语言/Python", "bookmarks": [_bookmark(2, name="Py", url="https://docs.python.org/3/", domain="docs.python.org", category="编程语言/Python", folder=["编程语言"])], "subcategories": {}, "count": 1}},
            "count": 1,
        },
    }

    display_hierarchy = cluster_module.build_display_hierarchy(
        clusterer,
        root_hierarchy,
        [{"name": "主要主题", "roots": ["数据库", "编程语言"]}],
        common_module.DEFAULT_DISPLAY_OPTIONS,
    )

    assert list(display_hierarchy) == ["数据库", "编程语言"]


def test_build_display_hierarchy_can_auto_group_actual_roots_without_profile_config():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    root_hierarchy = {
        "园艺": {
            "name": "园艺",
            "category": "园艺",
            "bookmarks": [
                _bookmark(1, name="Rose pruning", url="https://example.com/rose", domain="example.com", category="园艺", folder=["园艺"]),
                _bookmark(2, name="Compost guide", url="https://example.com/compost", domain="example.com", category="园艺", folder=["园艺"]),
            ],
            "subcategories": {},
            "count": 2,
        },
        "食谱": {
            "name": "食谱",
            "category": "食谱",
            "bookmarks": [
                _bookmark(3, name="Pasta", url="https://example.com/pasta", domain="example.com", category="食谱", folder=["食谱"]),
                _bookmark(4, name="Soup", url="https://example.com/soup", domain="example.com", category="食谱", folder=["食谱"]),
                _bookmark(5, name="Bread", url="https://example.com/bread", domain="example.com", category="食谱", folder=["食谱"]),
            ],
            "subcategories": {},
            "count": 3,
        },
        "待整理": {
            "name": "待整理",
            "category": "待整理",
            "bookmarks": [_bookmark(6, name="Unknown", url="https://example.com/unknown", domain="example.com", category="待整理", folder=["待整理"])],
            "subcategories": {},
            "count": 1,
        },
        "发现主题": {
            "name": "发现主题",
            "category": "发现主题",
            "bookmarks": [_bookmark(7, name="New topic", url="https://example.com/topic", domain="example.com", category="发现主题", folder=["发现主题"])],
            "subcategories": {},
            "count": 1,
        },
    }

    display_hierarchy = cluster_module.build_display_hierarchy(
        clusterer,
        root_hierarchy,
        [],
        common_module.DEFAULT_DISPLAY_OPTIONS,
    )

    assert list(display_hierarchy) == ["园艺", "食谱", "待整理"]
    assert "发现主题" in display_hierarchy["待整理"]["subcategories"]
    assert "待整理" not in display_hierarchy["待整理"]["subcategories"]


def test_build_display_hierarchy_restructures_tidy_root_by_confirmation_bucket():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    blocked = _bookmark(1, name="Blocked Q", url="https://zhuanlan.zhihu.com/p/1", domain="zhuanlan.zhihu.com", category="待整理", folder=["Inbox"])
    docker_one = _bookmark(2, name="Docker Docs", url="https://docs.docker.com/engine/", domain="docs.docker.com", category="待整理", folder=["Inbox"])
    docker_two = _bookmark(3, name="Docker Compose", url="https://example.com/docker-compose", domain="example.com", category="待整理", folder=["Inbox"])
    low_conf = _bookmark(4, name="Ambiguous Note", url="https://example.com/ambiguous", domain="example.com", category="待整理", folder=["Inbox"])
    for bookmark, bucket in (
        (blocked, "fetch_blocked"),
        (docker_one, "rule_gap"),
        (docker_two, "rule_gap"),
        (low_conf, "low_confidence"),
    ):
        bookmark["classification"].update(
            {
                "resource_type": "文档",
                "confirmation_bucket": bucket,
                "review_required": bucket == "fetch_blocked",
            }
        )

    root_hierarchy = {
        "待整理": {
            "name": "待整理",
            "category": "待整理",
            "bookmarks": [low_conf],
            "subcategories": {
                "Question": {
                    "name": "Question",
                    "category": "待整理",
                    "bookmarks": [blocked],
                    "subcategories": {},
                    "count": 1,
                    "merge_from_categories": ["待整理"],
                },
                "Docker": {
                    "name": "Docker",
                    "category": "待整理",
                    "bookmarks": [docker_one, docker_two],
                    "subcategories": {},
                    "count": 2,
                    "merge_from_categories": ["待整理"],
                },
            },
            "count": 4,
            "preserve_children": True,
        }
    }

    display_hierarchy = cluster_module.build_display_hierarchy(
        clusterer,
        root_hierarchy,
        [],
        common_module.DEFAULT_DISPLAY_OPTIONS,
    )

    tidy_root = display_hierarchy["待整理"]
    assert set(tidy_root["subcategories"]) == {"抓取受阻", "规则缺口", "低置信度"}
    assert "Docker" in tidy_root["subcategories"]["规则缺口"]["subcategories"]
    assert not tidy_root["subcategories"]["抓取受阻"]["subcategories"]
    assert [bookmark["name"] for bookmark in tidy_root["subcategories"]["抓取受阻"]["bookmarks"]] == ["Blocked Q"]
    assert [bookmark["name"] for bookmark in tidy_root["subcategories"]["低置信度"]["bookmarks"]] == ["Ambiguous Note"]
    assert "Question" in root_hierarchy["待整理"]["subcategories"]


def test_build_tidy_semantic_bundles_groups_rule_gap_bookmarks_by_semantic_label():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    docker_one = _bookmark(
        1,
        name="Docker Docs",
        url="https://docs.docker.com/engine/",
        domain="docs.docker.com",
        category="待整理",
        folder=["Inbox"],
        description="Docker engine manual",
        keywords="docker,containers",
    )
    docker_two = _bookmark(
        2,
        name="Docker Networking",
        url="https://example.com/docker-networking",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="Docker networking deep dive",
        keywords="docker,networking",
    )
    postgres_one = _bookmark(
        3,
        name="TiDB Compatibility",
        url="https://example.com/tidb-compat",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="TiDB PostgreSQL compatibility notes",
        keywords="tidb,postgresql",
    )
    postgres_two = _bookmark(
        4,
        name="PostgreSQL branch storage",
        url="https://example.com/postgres-branch-storage",
        domain="example.com",
        category="待整理",
        folder=["Inbox"],
        description="PostgreSQL storage internals",
        keywords="postgresql,storage",
    )
    for bookmark, topic, candidate in (
        (docker_one, "Docker", "容器/Docker"),
        (docker_two, "Docker", "容器/Docker"),
        (postgres_one, "PostgreSQL", "数据库/PostgreSQL"),
        (postgres_two, "PostgreSQL", "数据库/PostgreSQL"),
    ):
        bookmark["classification"].update(
            {
                "resource_type": "文档",
                "rule_confidence": 0.42,
                "cluster_hints": [topic],
                "open_topic_candidates": [{"topic": topic, "score": 4, "sources": ["title", "keywords"]}],
                "rule_candidates": [{"category": candidate, "root": candidate.split("/")[0], "leaf": candidate.split("/")[-1], "total": 24, "strong_evidence": False}],
                "rule_roots": [{"root": candidate.split("/")[0], "support": 0.55, "total": 24.0}],
                "confirmation_bucket": "rule_gap",
                "review_required": False,
            }
        )

    bundles, leftovers = cluster_module.build_tidy_semantic_bundles(
        clusterer,
        [docker_one, docker_two, postgres_one, postgres_two],
        bucket_name="规则缺口",
    )

    assert leftovers == []
    assert {bundle["bundle_label"] for bundle in bundles} == {"Docker", "PostgreSQL"}
    assert all(bundle["support_count"] == 2 for bundle in bundles)


def test_build_display_hierarchy_does_not_duplicate_discovery_root_when_already_grouped():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    root_hierarchy = {
        "发现主题": {
            "name": "发现主题",
            "category": "发现主题",
            "bookmarks": [{"name": "FastAPI", "url": "https://fastapi.tiangolo.com/"}],
            "subcategories": {},
            "count": 1,
            "preserve_children": True,
        }
    }
    root_groups = [{"name": "待整理", "roots": ["发现主题"]}]

    display_hierarchy = cluster_module.build_display_hierarchy(
        clusterer,
        root_hierarchy,
        root_groups,
        common_module.DEFAULT_DISPLAY_OPTIONS,
    )

    assert list(display_hierarchy) == ["待整理"]


def test_reset_pipeline_outputs_keeps_source_bookmark_file(tmp_path):
    source = tmp_path / "bookmarks.html"
    source.write_text("source", encoding="utf-8")
    config_file = tmp_path / "skill_config.json"
    config_file.write_text(
        json.dumps(
            {
                "input": {
                    "bookmark_file": "bookmarks.html",
                },
                "pipeline": {
                    "copied_bookmark_file": "bookmarks.html",
                    "parsed_file": "data/parsed.json",
                    "classified_file": "data/classified.json",
                    "clustering_file": "data/clustering.json",
                },
                "fetch_options": {
                    "cache_file": "data/enriched.json",
                },
                "output": {
                    "html_file": "output/organized.html",
                    "reports_directory": "output/reports",
                },
                "logging": {
                    "file": "logs/app.log",
                    "console": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = common_module.PipelineConfig.load(config_file)
    logger = common_module.configure_logging(config, "INFO")

    config.paths.parsed_file.parent.mkdir(parents=True, exist_ok=True)
    config.paths.parsed_file.write_text("{}", encoding="utf-8")
    config.paths.enriched_file.write_text("{}", encoding="utf-8")
    config.paths.classified_file.write_text("{}", encoding="utf-8")
    config.paths.clustering_file.write_text("{}", encoding="utf-8")
    config.paths.html_output.parent.mkdir(parents=True, exist_ok=True)
    config.paths.html_output.write_text("html", encoding="utf-8")
    config.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    (config.paths.reports_dir / "review.json").write_text("{}", encoding="utf-8")

    removed = reset_module.reset_pipeline_outputs(config, logger, protect={source.resolve()})

    assert removed >= 4
    assert source.exists()
    assert not config.paths.parsed_file.exists()
    assert not config.paths.enriched_file.exists()
    assert not config.paths.classified_file.exists()
    assert not config.paths.clustering_file.exists()
    assert not config.paths.html_output.exists()
    assert not config.paths.reports_dir.exists()
