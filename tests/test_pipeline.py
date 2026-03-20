import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from test_pipeline_support import build_metadata, write_enriched_fixture  # noqa: E402
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



def test_classifier_exports_open_topic_only_bookmarks_for_confirmation(tmp_path):
    rules_file = ROOT / "data" / "category_rules.json"
    classifier = classify_module.BookmarkClassifier(rules_file, {"confirm_threshold": 80})
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


def test_topic_collection_is_deterministic_for_same_inputs():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    bookmark = _bookmark(1, name="FastAPI 官方文档", url="https://fastapi.tiangolo.com/tutorial/", domain="fastapi.tiangolo.com", category="编程/Python Web", folder=["学习", "FastAPI"], resource_type="文档", description="python fastapi web api tutorial", keywords="python,fastapi,api")
    features = [clusterer.build_feature_set(bookmark) for _ in range(5)]
    topic_sets = [feature.topics for feature in features]
    assert all(topic_set == topic_sets[0] for topic_set in topic_sets[1:])
    assert features[0].primary_topic == "编程/Python Web"


def test_same_label_clusters_get_unique_subcategory_names():
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
    assert len(names) == 2
    assert len(set(names)) == 2
    assert names[0] == "编程/Python Web/学习/FastAPI"
    assert names[1] == "编程/Python Web/学习/FastAPI (2)"


def test_main_preserves_leaf_category_for_unsplit_clusters():
    clusterer = cluster_module.BookmarkClusterer(min_cluster_size=2)
    hierarchy = {
        "编程语言": clusterer.build_hierarchy([], "编程语言", threshold=20),
    }
    hierarchy["编程语言"]["bookmarks"] = []
    hierarchy["编程语言"]["subcategories"] = {}
    hierarchy["编程语言"]["count"] = 0

    python_cluster = [
        _bookmark(1, name="Python 官方文档", url="https://docs.python.org/3/", domain="docs.python.org", category="编程语言/Python", folder=["学习", "Python"]),
    ]
    rust_cluster = [
        _bookmark(2, name="Rust 官方文档", url="https://doc.rust-lang.org/book/", domain="doc.rust-lang.org", category="编程语言/Rust", folder=["学习", "Rust"]),
    ]

    for cluster in (python_cluster, rust_cluster):
        cluster_category = clusterer._derive_cluster_label(cluster, "其他/未分类")
        category_bucket = hierarchy["编程语言"]
        category_bucket["count"] += len(cluster)
        built = clusterer.build_hierarchy(cluster, cluster_category, threshold=20)
        if built.get("subcategories"):
            for name, item in built["subcategories"].items():
                unique_name = clusterer._unique_subcategory_name(category_bucket["subcategories"], name)
                category_bucket["subcategories"][unique_name] = item
            category_bucket["bookmarks"].extend(built.get("bookmarks", []))
        else:
            unique_name = clusterer._unique_subcategory_name(category_bucket["subcategories"], built["category"])
            category_bucket["subcategories"][unique_name] = {
                **built,
                "category": unique_name,
            }

    programming = hierarchy["编程语言"]
    assert sorted(programming["subcategories"].keys()) == ["编程语言/Python", "编程语言/Rust"]
    assert programming["bookmarks"] == []
