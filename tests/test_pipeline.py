import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_pipeline_support import write_enriched_fixture  # noqa: E402
from scripts_compat import common_module, parse_bookmarks_module, classify_module, cluster_module, html_module, copy_module, fetch_module  # noqa: E402


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


def test_copy_step_is_noop_for_same_file(tmp_path):
    source = tmp_path / "bookmarks.html"
    source.write_text("demo", encoding="utf-8")
    copied = copy_module.copy_bookmark_file(source, source)
    assert copied == source.resolve()
    assert source.read_text(encoding="utf-8") == "demo"


def test_classifier_uses_metadata_title_and_exports_confirmation(tmp_path):
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file, {"confirm_threshold": 90, "title_weight": 50})
    bookmark = {
        "id": "bookmark_1",
        "name": "收藏",
        "url": "https://www.python.org/dev/",
        "domain": "www.python.org",
        "original_folder_path": ["学习"],
        "metadata": {"title": "Python Packaging Guide", "description": ""},
    }
    category, score, _ = classifier.classify_bookmark(bookmark)
    assert category == "编程语言/Python"
    assert score > 0

    bookmark["classification"] = {"category": category, "score": score}
    report = tmp_path / "needs_confirmation.json"
    classify_module.export_confirmation_report([bookmark], report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    assert exported["count"] == 1


def test_cluster_and_generate_html():
    bookmarks = [
        {
            "id": f"bookmark_{index}",
            "name": f"Python Article {index}",
            "url": f"https://docs.python.org/{index}",
            "domain": "docs.python.org",
            "metadata": {"title": "Python Documentation", "description": "python guide", "keywords": "python", "resource_type": "文档"},
            "classification": {"category": "编程语言/Python", "all_scores": {"编程语言/Python": {"total": 90}}},
        }
        for index in range(12)
    ]
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=3)
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程语言/Python", threshold=5)
    html = html_module.BookmarkHTMLGenerator().generate_html({"编程语言/Python": hierarchy})
    assert "NETSCAPE-Bookmark-file-1" in html
    assert "编程语言/Python" in html


def test_rich_feature_clustering_groups_cross_domain_same_topic():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmarks = [
        _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/tutorial/", domain="fastapi.tiangolo.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api tutorial", keywords="python,fastapi,api"),
        _bookmark(2, name="FastAPI 部署指南", url="https://realpython.com/fastapi-deploy/", domain="realpython.com", category="编程/Python Web", folder=["学习", "Web"], resource_type="博客", description="python fastapi deployment web api", keywords="python,fastapi,deployment"),
        _bookmark(3, name="FastAPI 示例仓库", url="https://github.com/example/fastapi-service", domain="github.com", category="编程/Python Web", folder=["代码", "FastAPI"], resource_type="仓库", description="python fastapi service repository", keywords="python,fastapi,repository"),
    ]
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程/Python Web", threshold=1)
    assert len(hierarchy["subcategories"]) == 1
    only_cluster = next(iter(hierarchy["subcategories"].values()))
    assert only_cluster["count"] == 3
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


def test_high_quality_folder_reused_and_low_quality_folder_split():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    high_quality = [
        _bookmark(1, name="Django ORM Guide", url="https://docs.djangoproject.com/en/orm/", domain="docs.djangoproject.com", category="编程/Django", folder=["Backend", "Django"], resource_type="文档", description="django orm models querysets", keywords="django,orm,models"),
        _bookmark(2, name="Django Forms Guide", url="https://docs.djangoproject.com/en/forms/", domain="docs.djangoproject.com", category="编程/Django", folder=["Backend", "Django"], resource_type="文档", description="django forms validation", keywords="django,forms,validation"),
    ]
    high_hierarchy = clusterer.build_hierarchy(high_quality, "编程/Django", threshold=1)
    high_cluster = next(iter(high_hierarchy["subcategories"].values()))
    assert high_cluster["source_folder_reused"] is True
    assert high_cluster["source_folder_quality_score"] >= 0.68

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


def test_config_paths_are_resolved_relative_to_config_file(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "skill_config.json"
    config_file.write_text(
        json.dumps(
            {
                "input": {"bookmark_file": "inputs/bookmarks.html", "rules_file": str(ROOT / "data" / "category_rules.json")},
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


def test_cli_respects_config_and_creates_outputs(tmp_path):
    sample = ROOT / "tests" / "fixtures" / "sample_bookmarks.html"
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "skill_config.json"
    cfg.write_text(
        json.dumps(
            {
                "input": {"bookmark_file": "../fixtures/sample_bookmarks.html", "rules_file": str(ROOT / 'data' / 'category_rules.json')},
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
    assert (tmp_path / "runtime" / "logs" / "app.log").exists()
