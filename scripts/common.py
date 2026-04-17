#!/usr/bin/env python3
"""Shared helpers for the bookmarks pipeline."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "skill_config.json"
DEFAULT_TRUSTED_ACCESS_POLICY = {
    "enabled": True,
    "domain_suffixes": [
        "zhihu.com",
        "csdn.net",
        "github.com",
        "gitbook.com",
        "gitbook.io",
    ],
    "http_statuses": [403, 406, 429],
    "allow_reason_codes": ["timeout", "certificate", "other_error"],
    "domain_rules": [
        {
            "domain_suffixes": ["csdn.net", "csdn.com", "csdnimg.cn"],
            "http_statuses": [403, 404, 406, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        },
        {
            "domain_suffixes": ["zhihu.com"],
            "http_statuses": [403, 404, 406, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        },
        {
            "domain_suffixes": ["gitbook.com", "gitbook.io"],
            "http_statuses": [403, 404, 406, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        },
        {
            "domain_suffixes": ["github.com"],
            "http_statuses": [403, 406, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        },
    ],
}
DEFAULT_ROOT_GROUPS = [
    {"name": "数据库与存储", "roots": ["数据库"]},
    {"name": "编程开发", "roots": ["编程语言", "前端开发", "后端开发", "网络编程", "配置开发环境"]},
    {"name": "基础设施与运维", "roots": ["分布式系统", "云服务", "DevOps", "Linux系统", "安全"]},
    {"name": "AI与研究", "roots": ["机器学习", "算法与数据结构", "论文与研究"]},
    {"name": "工程工具与资源", "roots": ["开发工具", "文档与教程", "开源项目", "技术博客", "测试"]},
    {"name": "实验与杂项", "roots": ["实验项目", "其他/未分类"]},
]
DEFAULT_DISPLAY_OPTIONS = {
    "max_depth": 3,
    "collapse_single_child": True,
    "prefer_human_labels": True,
    "fallback_group_name": "实验与杂项",
}


@dataclass
class PipelinePaths:
    bookmark_file: Path
    copied_bookmark_file: Path
    parsed_file: Path
    enriched_file: Path
    classified_file: Path
    clustering_file: Path
    html_output: Path
    rules_file: Path
    reports_dir: Path
    log_file: Path
    duplicate_report_file: Path
    broken_links_report_file: Path
    confirmation_report_file: Path
    review_report_file: Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"[{record.levelname}] {record.getMessage()}"


DEFAULT_PATHS = {
    "bookmark_file": ROOT_DIR / "data" / "bookmarks.html",
    "copied_bookmark_file": ROOT_DIR / "data" / "bookmarks.html",
    "parsed_file": ROOT_DIR / "data" / "parsed_bookmarks.json",
    "enriched_file": ROOT_DIR / "data" / "bookmarks_with_info.json",
    "classified_file": ROOT_DIR / "data" / "classified_bookmarks.json",
    "clustering_file": ROOT_DIR / "data" / "clustering_result.json",
    "html_output": ROOT_DIR / "output" / "organized_bookmarks.html",
    "rules_file": ROOT_DIR / "data" / "category_rules.json",
    "reports_dir": ROOT_DIR / "output" / "reports",
    "log_file": ROOT_DIR / "logs" / "bookmarks_organizer.log",
    "duplicate_report_file": ROOT_DIR / "output" / "reports" / "duplicates.json",
    "broken_links_report_file": ROOT_DIR / "output" / "reports" / "broken_links.json",
    "confirmation_report_file": ROOT_DIR / "output" / "reports" / "needs_confirmation.json",
    "review_report_file": ROOT_DIR / "output" / "reports" / "review_queue.json",
}


def _resolve_path(value: Optional[str | Path], fallback: Path, base_dir: Path) -> Path:
    if value is None:
        return fallback
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def normalize_url(url: str, *, keep_fragment: bool = False) -> str:
    """Normalize bookmark URLs consistently across parse/fetch/cache stages."""
    candidate = (url or "").strip()
    if not candidate:
        return ""

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return candidate

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return candidate
    if hostname:
        netloc = hostname
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo = f"{userinfo}:{parsed.password}"
            netloc = f"{userinfo}@{netloc}"
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        if port and not default_port:
            netloc = f"{netloc}:{port}"
    else:
        netloc = parsed.netloc.lower()

    path = parsed.path or "/"
    fragment = parsed.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, path, parsed.query, fragment))


def normalize_bookmark_url(url: str) -> str:
    return normalize_url(url, keep_fragment=True)


def normalize_fetch_url(url: str) -> str:
    return normalize_url(url, keep_fragment=False)


def pipeline_generated_paths(config: "PipelineConfig") -> dict[str, list[Path]]:
    paths = config.paths
    files = [
        paths.copied_bookmark_file,
        paths.parsed_file,
        paths.enriched_file,
        paths.classified_file,
        paths.clustering_file,
        paths.html_output,
        paths.log_file,
    ]
    directories = [paths.reports_dir]
    return {
        "files": list(dict.fromkeys(files)),
        "directories": list(dict.fromkeys(directories)),
    }


class PipelineConfig:
    def __init__(self, raw: Dict[str, Any], config_path: Path):
        self.raw = raw
        self.config_path = config_path
        self.base_dir = config_path.resolve().parent if config_path.exists() else ROOT_DIR
        reports_dir = _resolve_path(raw.get("output", {}).get("reports_directory"), DEFAULT_PATHS["reports_dir"], self.base_dir)
        self.paths = PipelinePaths(
            bookmark_file=_resolve_path(raw.get("input", {}).get("bookmark_file"), DEFAULT_PATHS["bookmark_file"], self.base_dir),
            copied_bookmark_file=_resolve_path(raw.get("pipeline", {}).get("copied_bookmark_file"), DEFAULT_PATHS["copied_bookmark_file"], self.base_dir),
            parsed_file=_resolve_path(raw.get("pipeline", {}).get("parsed_file"), DEFAULT_PATHS["parsed_file"], self.base_dir),
            enriched_file=_resolve_path(raw.get("fetch_options", {}).get("cache_file"), DEFAULT_PATHS["enriched_file"], self.base_dir),
            classified_file=_resolve_path(raw.get("pipeline", {}).get("classified_file"), DEFAULT_PATHS["classified_file"], self.base_dir),
            clustering_file=_resolve_path(raw.get("pipeline", {}).get("clustering_file"), DEFAULT_PATHS["clustering_file"], self.base_dir),
            html_output=_resolve_path(raw.get("output", {}).get("html_file"), DEFAULT_PATHS["html_output"], self.base_dir),
            rules_file=_resolve_path(raw.get("input", {}).get("rules_file"), DEFAULT_PATHS["rules_file"], self.base_dir),
            reports_dir=reports_dir,
            log_file=_resolve_path(raw.get("logging", {}).get("file"), DEFAULT_PATHS["log_file"], self.base_dir),
            duplicate_report_file=_resolve_path(raw.get("output", {}).get("duplicate_report_file"), reports_dir / "duplicates.json", self.base_dir),
            broken_links_report_file=_resolve_path(raw.get("output", {}).get("broken_links_report_file"), reports_dir / "broken_links.json", self.base_dir),
            confirmation_report_file=_resolve_path(raw.get("output", {}).get("confirmation_report_file"), reports_dir / "needs_confirmation.json", self.base_dir),
            review_report_file=_resolve_path(raw.get("output", {}).get("review_report_file"), reports_dir / "review_queue.json", self.base_dir),
        )
        proxy_options = raw.get("fetch_options", {}).get("proxy", {})
        review_policy = raw.get("fetch_options", {}).get("review_policy", {})
        trusted_access = dict(DEFAULT_TRUSTED_ACCESS_POLICY)
        trusted_access.update(review_policy.get("trusted_access", {}))
        self.fetch_options = {
            "concurrent_limit": raw.get("fetch_options", {}).get("concurrent_limit", 15),
            "timeout": raw.get("fetch_options", {}).get("timeout", 15),
            "delay": raw.get("fetch_options", {}).get("delay", 0.8),
            "batch_size": raw.get("fetch_options", {}).get("batch_size", 50),
            "max_retries": raw.get("fetch_options", {}).get("max_retries", 2),
            "force_refetch": raw.get("fetch_options", {}).get("force_refetch", False),
            "user_agent": raw.get("fetch_options", {}).get(
                "user_agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            ),
            "proxy": {
                "enabled": proxy_options.get("enabled", False),
                "trust_env": proxy_options.get("trust_env", False),
                "http_proxy": proxy_options.get("http_proxy"),
                "https_proxy": proxy_options.get("https_proxy"),
                "all_proxy": proxy_options.get("all_proxy"),
            },
            "review_policy": {
                "trusted_access": trusted_access,
            },
        }
        self.classification_options = raw.get("classification_options", {})
        self.clustering_options = dict(raw.get("clustering_options", {}))
        self.clustering_options.setdefault("root_groups", DEFAULT_ROOT_GROUPS)
        display_options = dict(DEFAULT_DISPLAY_OPTIONS)
        display_options.update(self.clustering_options.get("display", {}))
        self.clustering_options["display"] = display_options
        self.logging_options = raw.get("logging", {})

    @classmethod
    def load(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> "PipelineConfig":
        config_path = config_path.resolve()
        raw: Dict[str, Any] = {}
        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(raw, config_path)


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--log-level", default=None, help="日志级别，例如 INFO/DEBUG")
    return parser


def configure_logging(config: PipelineConfig, level_override: Optional[str] = None) -> logging.Logger:
    log_file = config.paths.log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("bookmarks")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, (level_override or config.logging_options.get("level", "INFO")).upper(), logging.INFO))
    logger.propagate = False

    formatter = JsonFormatter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if config.logging_options.get("console", True):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def load_config_from_args(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig.load(args.config)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
