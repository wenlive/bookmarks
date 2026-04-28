import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common_module = _load('common_module', 'common.py')
copy_module = _load('copy_module', '1_copy_bookmark.py')
parse_bookmarks_module = _load('parse_bookmarks_module', '2_parse_bookmarks.py')
fetch_module = _load('fetch_module', '3_fetch_webpage_info.py')
classify_module = _load('classify_module', '4_classify_bookmarks.py')
cluster_module = _load('cluster_module', '5_cluster_bookmarks.py')
html_module = _load('html_module', '6_generate_html.py')
reset_module = _load('reset_module', 'reset_pipeline_state.py')
bootstrap_module = _load('bootstrap_module', 'generate_taxonomy_bootstrap.py')
apply_taxonomy_module = _load('apply_taxonomy_module', 'apply_taxonomy_response.py')
