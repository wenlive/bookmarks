import json
from pathlib import Path


def build_metadata(title: str, description: str, keywords: str, h1: str, content_preview: str, *,
                   page_type_hints: list[str] | None = None, site_name: str = '', brand_terms: list[str] | None = None) -> dict:
    page_type_hints = page_type_hints or []
    brand_terms = brand_terms or []
    page_signals = {
        'title': title,
        'description': description,
        'keywords': keywords,
        'h1': h1,
        'content_preview': content_preview,
        'main_text_preview': content_preview,
        'page_type_hints': page_type_hints,
        'headings': {'h1': [h1] if h1 else [], 'h2': []},
        'nav_text': [],
        'canonical_url': '',
        'lang': 'en',
        'og:title': title,
        'og:description': description,
        'og:site_name': site_name,
        'twitter:title': title,
        'twitter:description': description,
        'schema_types': [],
    }
    site_signals = {
        'homepage_url': '',
        'site_name': site_name,
        'site_type_candidates': page_type_hints,
        'content_language': 'en',
        'brand_terms': brand_terms,
        'homepage_fetch_status': 'skipped',
        'homepage_source': 'fixture',
    }
    return {
        'title': title,
        'description': description,
        'keywords': keywords,
        'h1': h1,
        'content_preview': content_preview,
        'fetch_status': 'success',
        'link_health': {
            'status': 'success',
            'reason_code': 'ok',
            'reason_label': '可访问',
            'review_required': False,
            'status_code': 200,
            'error': '',
        },
        'status_code': 200,
        'page_signals': page_signals,
        'site_signals': site_signals,
        'site_profile': {
            'schema_version': 'site_profile/v1',
            'url': {
                'normalized_url': '',
                'scheme': 'https',
                'netloc': '',
                'subdomain': '',
                'registrable_domain': '',
                'path_segments': [],
                'query_keys': [],
            },
            'page': page_signals,
            'site': site_signals,
        },
        'metadata_schema_version': 'site_profile/v1',
    }


def write_enriched_fixture(parsed_file: Path, output_file: Path) -> None:
    data = json.loads(parsed_file.read_text(encoding='utf-8'))
    for bookmark in data['bookmarks']:
        if 'python' in bookmark['url']:
            bookmark['metadata'] = build_metadata(
                'Python Documentation', 'Official python docs', 'python,docs', 'Python', 'python docs',
                page_type_hints=['documentation'], site_name='Python', brand_terms=['python'],
            )
        else:
            bookmark['metadata'] = build_metadata(
                'OpenAI Research', 'AI updates', 'ai,llm', 'Research', 'OpenAI AI research',
                page_type_hints=['research'], site_name='OpenAI', brand_terms=['openai'],
            )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps({'bookmarks': data['bookmarks'], 'stats': {}}, ensure_ascii=False, indent=2), encoding='utf-8')
