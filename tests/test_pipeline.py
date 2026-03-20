import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_pipeline_support import build_metadata, write_enriched_fixture  # noqa: E402
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


def test_classifier_outputs_multidimensional_labels_and_confirmation_report(tmp_path):
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file, {"confirm_threshold": 90, "title_weight": 50})
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
    assert classification["category"] == "编程语言/Python"
    assert "编程语言/Python" in classification["primary_topics"]
    assert classification["resource_type"] in {"文档", "教程"}
    assert "学习" in classification["intent_labels"]
    assert classification["score"] > 0
    assert "classification_evidence" in classification

    bookmark["classification"] = classification
    report = tmp_path / "needs_confirmation.json"
    classify_module.export_confirmation_report([bookmark], report)
    exported = json.loads(report.read_text(encoding="utf-8"))
    assert exported["count"] == 1
    assert exported["bookmarks"][0]["primary_topics"][0] == "编程语言/Python"


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

    metadata = fetch_module.asyncio.run(fetch_module.fetch_with_aiohttp(session, deep_url, timeout=3, max_retries=0))

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


def test_classifier_keeps_rule_and_open_topics_together():
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file)
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



def test_classifier_distinguishes_resource_types_within_same_topic():
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file)
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
    assert "分布式系统/Kubernetes" in doc_classification["primary_topics"]
    assert "分布式系统/Kubernetes" in blog_classification["primary_topics"]
    assert doc_classification["resource_type"] == "文档"
    assert blog_classification["resource_type"] == "博客"
    assert "官方" in doc_classification["quality_signals"]
    assert "社区" in blog_classification["quality_signals"]



def test_classifier_treats_folder_as_weak_prior_and_reports_low_confidence():
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file, {"confirm_threshold": 80})
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
    assert "安全" in classification["primary_topics"]
    assert "分布式系统/Kubernetes" in classification["secondary_topics"] or any(
        item["topic"] == "分布式系统/Kubernetes" for item in classification["classification_evidence"]["topic_scores"]
    )
    assert classification["folder_alignment_score"] == 0
    assert stats["low_confidence_items"][0]["id"] == "bookmark_folder_bias"
    assert "resource_type_distribution" in stats
    assert "uncovered_topic_candidates" in stats
