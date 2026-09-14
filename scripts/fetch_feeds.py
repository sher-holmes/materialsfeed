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
from datetime import datetime, timezone

import feedparser
import requests
from deep_translator import GoogleTranslator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "sources.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "feed.json")

MAX_PER_SOURCE = 15    # 每個來源單次最多保留幾篇
MAX_TOTAL = 400        # 全站最多保留幾篇（累積去重後）
REQUEST_DELAY_SEC = 1  # 每個 RSS 來源間隔，避免對期刊網站造成負擔
IMAGE_FETCH_TIMEOUT = 6
ENRICH_WORKERS = 8     # 平行處理翻譯+抓縮圖的執行緒數量

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
    """翻譯失敗回傳 None，讓程式下次執行時再試一次（不會卡住流程）。"""
    try:
        result = GoogleTranslator(source="auto", target="zh-TW").translate(title)
        return result.strip() if result else None
    except Exception as e:
        print(f"[warn] 翻譯失敗：{title[:40]}... ({e})")
        return None


def
