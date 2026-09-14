"""
抓取 config/sources.json 中列出的所有期刊 RSS，整理成 data/feed.json。
新項目會另外嘗試：(1) 把標題翻成繁體中文 (2) 抓文章頁面的 og:image 當縮圖。
已經處理過的舊項目不會重複翻譯/重複抓圖，避免每天執行時間爆增。
由 .github/workflows/update-feed.yml 每日排程執行，也可手動執行：
    python scripts/fetch_feeds.py
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "feed.json")

MAX_PER_SOURCE = 15    # 每個來源單次最多保留幾篇
MAX_TOTAL = 400        # 全站最多保留幾篇（累積去重後）的安全上限
MAX_AGE_DAYS = 7       # 只保留最近幾天內的文章；沒有日期資訊的項目無法判斷新舊，予以保留
REQUEST_DELAY_SEC = 1  # 每個 RSS 來源間隔，避免對期刊網站造成負擔
IMAGE_FETCH_TIMEOUT = 6
IMAGE_WORKERS = 8            # 平行抓縮圖的執行緒數量（不受翻譯服務限流影響）
TRANSLATE_MIN_INTERVAL = 0.5   # 每次翻譯間隔（秒）
TRANSLATE_MAX_RETRIES = 2      # 單一引擎失敗時的重試次數
ENRICH_PER_RUN_LIMIT = 150     # 單次執行最多翻譯/抓圖幾篇，優先處理最新文章，其餘留到下次執行

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MaterialsFeedBot/1.0; +https://github.com/)"}

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


def translate_title(title):
    """依序嘗試不同翻譯引擎並重試；GitHub Actions 的伺服器 IP 常被 Google 免費翻譯介面判定為機房流量而限流，
    所以優先試 Google，失敗則換 MyMemory 這個對機房 IP 較寬容的引擎。全部失敗回傳 None，讓程式下次執行時再試。
    """
    engines = [
        ("Google", lambda t: GoogleTranslator(source="auto", target="zh-TW").translate(t)),
        ("MyMemory", lambda t: MyMemoryTranslator(source="en-GB", target="zh-TW").translate(t)),
    ]
    for name, translate_fn in engines:
        for attempt in range(TRANSLATE_MAX_RETRIES + 1):
            try:
                result = translate_fn(title)
                if result:
                    return result.strip()
            except Exception as e:
                if attempt < TRANSLATE_MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                print(f"[warn] {name} 翻譯失敗：{title[:40]}... ({e})")
    return None


def fetch_og_image(url):
    """只嘗試一次；失敗或找不到就永久記錄為 None，不會每天重抓耗時間。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=IMAGE_FETCH_TIMEOUT)
        html = resp.text[:200000]  # 只看前段，避免超大頁面拖慢速度
        m = OG_IMAGE_RE.search(html) or OG_IMAGE_RE_ALT.search(html)
        if m:
            image_url = m.group(1).strip()
            if image_url.startswith("http"):
                return image_url
    except Exception as e:
        print(f"[warn] 抓縮圖失敗：{url[:60]}... ({e})")
    return None


def enrich_translations(items):
    """依序翻譯並控制間隔，避免超過翻譯服務的速率限制而被拒絕。"""
    for it in items:
        it["title_zh"] = translate_title(it["title"])
        time.sleep(TRANSLATE_MIN_INTERVAL)


def enrich_images(items):
    """平行抓縮圖；抓取不同期刊網站不受翻譯服務的限流規則影響，可以同時進行。"""
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        futures = {pool.submit(fetch_og_image, it["link"]): it for it in items}
        done = 0
        for future in as_completed(futures):
            it = futures[future]
            it["image"] = future.result()
            it["image_checked"] = True
            done += 1
            if done % 20 == 0:
                print(f"[info] 縮圖抓取進度：{done}/{len(items)}")


def is_recent(item):
    """沒有日期資訊的項目（多半是 sciencedirect 的 RSS）無法判斷新舊，予以保留。"""
    pub = item.get("published")
    if not pub:
        return True
    try:
        dt = datetime.fromisoformat(pub)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - dt <= timedelta(days=MAX_AGE_DAYS)


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
        try:
            resp = requests.get(source["url"], headers=HEADERS, timeout=10)
            status = resp.status_code
            if status != 200:
                print(f"[error] {source['name']}: HTTP {status}（網站拒絕或網址失效，需要去官網重新確認 RSS 網址）")
                return items
            feed = feedparser.parse(resp.content)
        except requests.RequestException as e:
            print(f"[error] {source['name']}: 連線失敗 ({e})")
            return items

        if not feed.entries:
            reason = getattr(feed, "bozo_exception", "回傳內容沒有文章項目，網址可能已失效")
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
            prev = existing_items.get(it["link"])
            if prev:
                it["title_zh"] = prev.get("title_zh")
                it["image"] = prev.get("image")
                it["image_checked"] = prev.get("image_checked", False)
            else:
                it["title_zh"] = None
                it["image"] = None
                it["image_checked"] = False
            all_items[it["link"]] = it
        if i < len(sources) - 1:
            time.sleep(REQUEST_DELAY_SEC)

    # 只保留最近 MAX_AGE_DAYS 天的文章，順便讓翻譯失敗的待處理清單不會無限累積
    before_count = len(all_items)
    all_items = {link: it for link, it in all_items.items() if is_recent(it)}
    dropped = before_count - len(all_items)
    if dropped:
        print(f"[info] 依「近 {MAX_AGE_DAYS} 天」規則過濾掉 {dropped} 篇較舊的文章")

    to_enrich = [it for it in all_items.values() if it.get("title_zh") is None]
    to_enrich.sort(key=lambda it: it["published"] or "", reverse=True)
    if len(to_enrich) > ENRICH_PER_RUN_LIMIT:
        print(f"[info] 待處理 {len(to_enrich)} 篇，本次只優先處理最新 {ENRICH_PER_RUN_LIMIT} 篇，其餘留到下次執行")
        to_enrich = to_enrich[:ENRICH_PER_RUN_LIMIT]

    print(f"[info] 本次需新增/重新處理翻譯與縮圖：{len(to_enrich)} 篇")

    if to_enrich:
        print(f"[info] 翻譯（依序執行，控制在每秒 {1/TRANSLATE_MIN_INTERVAL:.1f} 次以內）...")
        enrich_translations(to_enrich)
        print(f"[info] 抓縮圖（{IMAGE_WORKERS} 條執行緒平行處理）...")
        enrich_images(to_enrich)
        for it in to_enrich:
            all_items[it["link"]] = it

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
