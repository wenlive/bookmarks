#!/usr/bin/env python3
"""步骤3: 获取网页信息（使用MCP web_reader或备用aiohttp）"""
import json
import asyncio
import aiohttp
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Dict
import sys

# 配置
CONCURRENT_LIMIT = 15
TIMEOUT = 15
DELAY = 0.8
MAX_RETRIES = 2
BATCH_SIZE = 50  # 每批处理数量


async def fetch_with_aiohttp(session: aiohttp.ClientSession, url: str) -> Optional[Dict]:
    """使用aiohttp获取网页信息"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as response:
            if response.status == 200:
                html = await response.text()
                soup = BeautifulSoup(html, 'lxml')

                # 提取标题
                title = soup.find('title')
                title_text = title.get_text(strip=True) if title else ''

                # 提取meta信息
                description = ''
                keywords = ''

                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc:
                    description = meta_desc.get('content', '')

                meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
                if meta_keywords:
                    keywords = meta_keywords.get('content', '')

                # 提取h1
                h1 = soup.find('h1')
                h1_text = h1.get_text(strip=True) if h1 else ''

                # 提取内容预览
                body = soup.find('body')
                content_preview = ''
                if body:
                    text = body.get_text(separator=' ', strip=True)
                    content_preview = text[:500]

                return {
                    "title": title_text,
                    "description": description,
                    "keywords": keywords,
                    "h1": h1_text,
                    "content_preview": content_preview,
                    "fetch_status": "success"
                }
    except asyncio.TimeoutError:
        return {"fetch_status": "timeout", "error": "Request timeout"}
    except aiohttp.ClientError as e:
        return {"fetch_status": "error", "error": str(e)}
    except Exception as e:
        return {"fetch_status": "error", "error": str(e)}

    return {"fetch_status": "error", "error": "Unknown error"}


async def process_batch(bookmarks: list, start_idx: int) -> list:
    """处理一批书签"""
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT)
    results = []

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for bm in bookmarks:
            if bm['url'].startswith(('http://', 'https://')):
                tasks.append(fetch_with_aiohttp(session, bm['url']))
            else:
                tasks.append(asyncio.sleep(0, result={"fetch_status": "skipped", "error": "Invalid URL"}))

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for bm, result in zip(bookmarks, batch_results):
            if isinstance(result, Exception):
                result = {"fetch_status": "error", "error": str(result)}

            bm_with_info = bm.copy()
            bm_with_info['metadata'] = result
            results.append(bm_with_info)

            # 延迟以避免被封
            await asyncio.sleep(DELAY)

    return results


async def fetch_webpage_info_async(input_file: Path, output_file: Path):
    """异步获取所有书签的网页信息"""
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    bookmarks = data['bookmarks']
    total = len(bookmarks)

    print(f"开始获取网页信息，共 {total} 个书签")
    print(f"并发数: {CONCURRENT_LIMIT}, 超时: {TIMEOUT}秒, 延迟: {DELAY}秒")

    results = []
    success_count = 0
    fail_count = 0

    # 分批处理
    for i in range(0, total, BATCH_SIZE):
        batch = bookmarks[i:i + BATCH_SIZE]
        print(f"\n处理进度: {i}/{total} ({i*100//total}%)")

        batch_results = await process_batch(batch, i)
        results.extend(batch_results)

        # 统计
        for r in batch_results:
            if r['metadata'].get('fetch_status') == 'success':
                success_count += 1
            else:
                fail_count += 1

        # 每批保存一次进度
        temp_data = {
            "bookmarks": results,
            "stats": {
                "total_bookmarks": len(results),
                "success_count": success_count,
                "fail_count": fail_count,
                "progress": f"{len(results)}/{total}"
            }
        }
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(temp_data, f, ensure_ascii=False, indent=2)

    # 最终结果
    final_data = {
        "bookmarks": results,
        "stats": {
            "total_bookmarks": total,
            "success_count": success_count,
            "fail_count": fail_count,
            "success_rate": f"{success_count*100/total:.1f}%"
        }
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 网页信息获取完成: {output_file}")
    print(f"  成功: {success_count}/{total} ({success_count*100/total:.1f}%)")
    print(f"  失败: {fail_count}/{total}")


def main():
    input_file = Path("/Users/lipoqi/data/playground/bookmarks/data/parsed_bookmarks.json")
    output_file = Path("/Users/lipoqi/data/playground/bookmarks/data/bookmarks_with_info.json")

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return

    asyncio.run(fetch_webpage_info_async(input_file, output_file))


if __name__ == "__main__":
    main()
