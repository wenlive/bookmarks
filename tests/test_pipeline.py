import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_pipeline_support import write_enriched_fixture  # noqa: E402
from scripts_compat import common_module, parse_bookmarks_module, classify_module, cluster_module, html_module, copy_module, fetch_module  # noqa: E402


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
            "metadata": {"title": "Python Documentation", "description": "python guide", "keywords": "python"},
            "classification": {"category": "编程语言/Python"},
        }
        for index in range(12)
    ]
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=3)
    hierarchy = clusterer.build_hierarchy(bookmarks, "编程语言/Python", threshold=5)
    html = html_module.BookmarkHTMLGenerator().generate_html({"编程语言/Python": hierarchy})
    assert "NETSCAPE-Bookmark-file-1" in html
    assert "编程语言/Python" in html


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
