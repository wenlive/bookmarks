import json
from pathlib import Path


def write_enriched_fixture(parsed_file: Path, output_file: Path) -> None:
    data = json.loads(parsed_file.read_text(encoding="utf-8"))
    for bookmark in data["bookmarks"]:
        if "python" in bookmark["url"]:
            bookmark["metadata"] = {
                "title": "Python Documentation",
                "description": "Official python docs",
                "keywords": "python,docs",
                "h1": "Python",
                "content_preview": "python docs",
                "fetch_status": "success",
                "status_code": 200,
            }
        else:
            bookmark["metadata"] = {
                "title": "OpenAI Research",
                "description": "AI updates",
                "keywords": "ai,llm",
                "h1": "Research",
                "content_preview": "OpenAI AI research",
                "fetch_status": "success",
                "status_code": 200,
            }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps({"bookmarks": data["bookmarks"], "stats": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
