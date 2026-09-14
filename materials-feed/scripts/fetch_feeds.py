"""
抓取 config/sources.json 中列出的所有期刊 RSS，整理成 data/feed.json。
由 .github/workflows/update-feed.yml 每日排程執行，也可手動執行：
    python scripts/fetch_feeds.py
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import feedparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "feed.json")

MAX_PER_SOURCE = 15   # 每個來源單次最多保留幾篇
MAX_TOTAL = 400        # 全站最多保留幾篇（累積去重後）
REQUEST_DELAY_SEC = 1  # 每個來源間隔，避免對期刊網站造成負擔

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MaterialsFeedBot/1.0; +https://github.com/)"}


def parse_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def clean_summary(summary, limit=220):
    text = re.sub(r"<[^>]+>", "", summary or "")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def fetch_source(source):
    items = []
    try:
        feed = feedparser.parse(source["url"], request_headers=HEADERS)
        if not feed.entries:
            reason = getattr(feed, "bozo_exception", "no entries returned")
            print(f"[warn] {source['name']}: {reason}")
            return items
        for entry in feed.entries[:MAX_PER_SOURCE]:
            link = (entry.get("link") or "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            dt = parse_date(entry)
            items.append({
                "title": title,
                "link": link,
                "summary": clean_summary(entry.get("summary", "")),
                "source": source["name"],
                "category": source.get("category", "general"),
                "published": dt.isoformat() if dt else None,
            })
        print(f"[ok] {source['name']}: {len(items)} 篇")
    except Exception as e:
        print(f"[error] {source['name']}: {e}")
    return items


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        sources = json.load(f)

    # 保留舊資料，新抓取結果以 link 為 key 覆蓋/合併，避免單次來源失敗就掉資料
    existing_items = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
            for it in prev.get("items", []):
                if it.get("link"):
                    existing_items[it["link"]] = it
        except Exception as e:
            print(f"[warn] 無法讀取既有 feed.json：{e}")

    all_items = dict(existing_items)
    ok_count, fail_count = 0, 0

    for i, source in enumerate(sources):
        fetched = fetch_source(source)
        if fetched:
            ok_count += 1
        else:
            fail_count += 1
        for it in fetched:
            all_items[it["link"]] = it
        if i < len(sources) - 1:
            time.sleep(REQUEST_DELAY_SEC)

    items = list(all_items.values())
    items.sort(key=lambda it: it["published"] or "", reverse=True)
    items = items[:MAX_TOTAL]

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources_total": len(sources),
        "sources_ok": ok_count,
        "sources_failed": fail_count,
        "items": items,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n完成：{ok_count} 個來源成功、{fail_count} 個失敗，共 {len(items)} 篇文章。")


if __name__ == "__main__":
    main()
