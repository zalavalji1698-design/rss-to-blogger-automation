#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS to Blogger Automation
Production-ready automation with:
- RSS feed parsing + metadata fallback
- Playwright browser rendering for dynamic websites
- Requests + BeautifulSoup fallback
- Pending queue + duplicate protection
- Blogger API rate-limit handling
- Adsterra HTML blocks preserved
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

# =====================================
# Logging Setup
# =====================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("automation.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


def log_rss(msg: str):
    logger.info(f"[RSS] {msg}")


def log_browser(msg: str):
    logger.info(f"[BROWSER] {msg}")


def log_scraper(msg: str):
    logger.info(f"[SCRAPER] {msg}")


def log_validation(msg: str):
    logger.info(f"[VALIDATION] {msg}")


def log_blogger(msg: str):
    logger.info(f"[BLOGGER] {msg}")


def log_queue(msg: str):
    logger.info(f"[QUEUE] {msg}")


# =====================================
# Environment Configuration
# =====================================
BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "").strip()
CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
RSS_FEED_URLS = os.environ.get("RSS_FEED_URLS", "").strip()
MAX_POSTS_PER_RUN = max(1, int(os.environ.get("MAX_POSTS_PER_RUN", "5").strip() or "5"))

POSTED_FILE = "posted_urls.json"
PENDING_FILE = "pending_posts.json"
AUTO_POST_LABEL = "RSS Auto Post"

# =====================================
# Adsterra Ads Configuration (preserved)
# =====================================
ADSTERRA_ADS = {
    "banner_468x60": """<div style="text-align: center; margin: 20px 0; padding: 10px;">
<script>
  atOptions = {
    'key' : 'c02c0defb48d592cc7e25fbb0115e63a',
    'format' : 'iframe',
    'height' : 60,
    'width' : 468,
    'params' : {}
  };
</script>
<script src="https://sponsorinserttimeout.com/c02c0defb48d592cc7e25fbb0115e63a/invoke.js"></script>
</div>""",

    "banner_728x90": """<div style="text-align: center; margin: 20px 0; padding: 10px;">
<script>
  atOptions = {
    'key' : 'a0a802a872bc486ec685fb758c8eef4b',
    'format' : 'iframe',
    'height' : 90,
    'width' : 728,
    'params' : {}
  };
</script>
<script src="https://sponsorinserttimeout.com/a0a802a872bc486ec685fb758c8eef4b/invoke.js"></script>
</div>""",

    "native_banner": """<div style="margin: 20px 0; padding: 10px; background-color: #f0f0f0; border-radius: 5px;">
<script async="async" data-cfasync="false" src="https://sponsorinserttimeout.com/98ba824931c676f939dd9b860ee5df26/invoke.js"></script>
<div id="container-98ba824931c676f939dd9b860ee5df26"></div>
</div>""",

    "social_bar": """<div style="margin: 20px 0; padding: 10px;">
<script src="https://sponsorinserttimeout.com/ec/86/73/ec867370afff042880ed8bcfe2b6caaa.js"></script>
</div>""",
}

# =====================================
# Utility functions
# =====================================
def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in pairs if not k.lower().startswith(tuple(["utm_", "fbclid", "gclid", "igshid", "mc_cid", "mc_eid"]))]
        clean_query = urlencode(filtered)
        path = parsed.path.rstrip("/")
        clean_url = urlunparse((parsed.scheme, parsed.netloc, path or "/", parsed.params, clean_query, ""))
        return clean_url.strip()
    except Exception:
        return str(url).strip()


# =====================================
# Posted URLs Manager
# =====================================
class PostedURLsManager:
    def __init__(self, file_path: str = POSTED_FILE):
        self.file_path = file_path
        self.urls: Set[str] = self._load()

    def _load(self) -> Set[str]:
        try:
            if not os.path.exists(self.file_path):
                return set()
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {normalize_url(str(x).strip()) for x in data if x and str(x).strip()}
            return set()
        except Exception:
            return set()

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(sorted(list(self.urls)), f, ensure_ascii=False, indent=2)
            log_queue(f"Posted URLs saved ({len(self.urls)} total)")
        except Exception as exc:
            logger.error(f"❌ Save error: {exc}")

    def is_posted(self, url: str) -> bool:
        return normalize_url(url) in self.urls

    def add(self, url: str):
        clean = normalize_url(url)
        if clean:
            self.urls.add(clean)


# =====================================
# Pending Queue Manager
# =====================================
class PendingQueue:
    def __init__(self, file_path: str = PENDING_FILE):
        self.file_path = file_path
        self.queue: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        try:
            if not os.path.exists(self.file_path):
                return []
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
            return []
        except Exception:
            return []

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
            log_queue(f"Pending queue saved ({len(self.queue)} items)")
        except Exception as exc:
            logger.error(f"❌ Pending queue save error: {exc}")

    def push(self, item: Dict[str, Any]):
        if not item:
            return
        url = normalize_url(str(item.get("url", "") or ""))
        if not url:
            return
        for existing in self.queue:
            if normalize_url(str(existing.get("url", "") or "")) == url:
                return
        self.queue.append(item)

    def remove(self, url: str):
        clean = normalize_url(url)
        self.queue = [entry for entry in self.queue if normalize_url(str(entry.get("url", "") or "")) != clean]

    def pop_batch(self, max_items: int) -> List[Dict[str, Any]]:
        batch = self.queue[:max_items]
        self.queue = self.queue[max_items:]
        return batch


# =====================================
# HTML / metadata extraction fallback
# =====================================
class ContentScraper:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            backoff_factor=1,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        })
        return session

    def fetch(self, url: str) -> Optional[str]:
        try:
            log_browser(f"Fetching URL: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except Exception as exc:
            logger.error(f"[SCRAPER] requests fetch failed: {exc}")
            return None

    def extract_from_html(self, html: str, base_url: str) -> Dict[str, Optional[str]]:
        soup = BeautifulSoup(html, "html.parser")
        return {
            "title": self._extract_title(soup, base_url),
            "description": self._extract_description(soup),
            "image": self._extract_image(soup, base_url),
            "video": self._extract_video(soup, base_url),
        }

    def _extract_title(self, soup: BeautifulSoup, base_url: str) -> str:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)
            if title:
                log_scraper(f"Title found via <h1>: {title[:80]}")
                return title

        # JSON-LD headline
        jsonld = self._extract_jsonld(soup)
        if jsonld:
            headline = jsonld.get("headline") or jsonld.get("name")
            if headline:
                log_scraper("Title found via JSON-LD")
                return str(headline).strip()

        meta = soup.find("meta", property="og:title")
        if meta and meta.get("content"):
            log_scraper("Title found via og:title")
            return str(meta["content"]).strip()

        meta = soup.find("meta", attrs={"name": "twitter:title"})
        if meta and meta.get("content"):
            log_scraper("Title found via twitter:title")
            return str(meta["content"]).strip()

        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(" ", strip=True)
            if title:
                log_scraper("Title found via <title>")
                return title

        return urlparse(base_url).netloc

    def _extract_description(self, soup: BeautifulSoup) -> str:
        jsonld = self._extract_jsonld(soup)
        if jsonld:
            description = jsonld.get("description")
            if description and len(str(description)) > 20:
                log_scraper("Description found via JSON-LD")
                return str(description)[:1000]

        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            text = str(meta["content"]).strip()
            if text and len(text) > 20:
                log_scraper("Description found via meta description")
                return text[:1000]

        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta and meta.get("content"):
            text = str(meta["content"]).strip()
            if text and len(text) > 20:
                log_scraper("Description found via og:description")
                return text[:1000]

        article = soup.find("article")
        if article:
            text = article.get_text(" ", strip=True)
            if text and len(text) > 100:
                log_scraper("Description found via <article>")
                return text[:1000]

        for tag in soup.find_all(["p", "div", "summary"], limit=8):
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 80 and not re.search(r"ads?\b|subscribe\b|follow us\b|copyright\b|navigation\b", text, flags=re.I):
                log_scraper("Description found via paragraph")
                return text[:1000]

        return "વધુ માહિતી માટે મૂળ લેખ વાંચો."

    def _extract_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        # JSON-LD image fields
        jsonld = self._extract_jsonld(soup)
        if jsonld:
            image = jsonld.get("image")
            if isinstance(image, dict):
                candidate = image.get("url") or image.get("contentUrl")
            elif isinstance(image, list):
                candidate = image[0].get("url") if isinstance(image[0], dict) else str(image[0])
            else:
                candidate = image
            if candidate and self._is_valid_image(str(candidate)):
                log_scraper(f"Image found via JSON-LD: {candidate}")
                return urljoin(base_url, str(candidate))

        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            candidate = str(og_image["content"]).strip()
            if self._is_valid_image(candidate):
                log_scraper("Image found via og:image")
                return candidate

        twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
        if twitter_image and twitter_image.get("content"):
            candidate = str(twitter_image["content"]).strip()
            if self._is_valid_image(candidate):
                log_scraper("Image found via twitter:image")
                return candidate

        for img in soup.find_all("img", limit=30):
            src_candidates = []
            for attr in ["src", "data-src", "data-lazy-src", "data-original", "srcset"]:
                value = img.get(attr)
                if not value:
                    continue
                if attr == "srcset":
                    parts = [p.split()[0] for p in value.split(",") if p.strip()]
                    src_candidates.extend(parts)
                else:
                    src_candidates.append(value)
            for candidate in src_candidates:
                url = urljoin(base_url, str(candidate).strip())
                if self._is_valid_image(url):
                    log_scraper(f"Image found via <img>: {url}")
                    return url

        for tag in soup.find_all(style=True, limit=20):
            style = tag.get("style", "")
            match = re.search(r"background(?:-image)?:\s*url\(([^)]+)\)", style, flags=re.I)
            if match:
                candidate = match.group(1).strip('"\'')
                url = urljoin(base_url, candidate)
                if self._is_valid_image(url):
                    log_scraper("Image found via CSS background-image")
                    return url

        return None

    def _extract_video(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        jsonld = self._extract_jsonld(soup)
        if jsonld:
            for key in ["contentUrl", "embedUrl", "url"]:
                candidate = jsonld.get(key)
                if candidate and self._is_valid_video(str(candidate)):
                    log_scraper(f"Video found via JSON-LD: {candidate}")
                    return str(candidate)

        og_video = soup.find("meta", property="og:video")
        if og_video and og_video.get("content"):
            candidate = str(og_video["content"]).strip()
            if self._is_valid_video(candidate):
                log_scraper("Video found via og:video")
                return candidate

        for iframe in soup.find_all("iframe", limit=10):
            src = (iframe.get("src") or iframe.get("data-src") or "").strip()
            if src and self._is_valid_video(src):
                log_scraper(f"Iframe video found: {src}")
                return src

        for video_tag in soup.find_all("video", limit=10):
            src = video_tag.get("src")
            if src and self._is_valid_video(src):
                log_scraper(f"Video found via <video>: {src}")
                return urljoin(base_url, src)
            for source in video_tag.find_all("source"):
                src = source.get("src")
                if src and self._is_valid_video(src):
                    log_scraper(f"Video found via <source>: {src}")
                    return urljoin(base_url, src)

        for element in soup.find_all(["div", "a", "source"], limit=30):
            for attr in ["data-video", "data-video-url", "data-src", "poster"]:
                candidate = element.get(attr)
                if candidate and self._is_valid_video(str(candidate)):
                    log_scraper(f"Video found via attribute {attr}: {candidate}")
                    return urljoin(base_url, str(candidate))

        text = soup.get_text(" ", strip=True)
        match = re.search(r"https?:\\?/\\?/[^\s\"'<>]+\.(mp4|webm|m4v|ogg|ogv|m3u8)(?:\?[^\s\"'<>]+)?", text, flags=re.I)
        if match:
            candidate = match.group(0)
            if self._is_valid_video(candidate):
                log_scraper(f"Video found in page text: {candidate}")
                return candidate

        return None

    def _extract_jsonld(self, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
        try:
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                raw = script.string or script.get_text(strip=True)
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        data = data[0]
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _is_valid_image(self, url: str) -> bool:
        if not url:
            return False
        lowered = url.lower()
        if any(token in lowered for token in ["ads", "tracking", "pixel", "spacer", "1x1", "favicon", "logo"]):
            return False
        if any(lowered.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"]):
            return True
        if any(cdn in lowered for cdn in ["cloudinary", "imgix", "imageserve", "pbs.twimg", "imgur"]):
            return True
        return False

    def _is_valid_video(self, url: str) -> bool:
        if not url:
            return False
        lowered = url.lower()
        # direct media file or streaming
        if any(ext in lowered for ext in [".mp4", ".webm", ".m4v", ".ogg", ".ogv", ".m3u8"]):
            return True
        if any(provider in lowered for provider in ["youtube", "youtu.be", "vimeo", "dailymotion", "rumble", "odysee"]):
            return True
        return False


# =====================================
# Playwright Browser Scraper
# =====================================
class BrowserScraper:
    def __init__(self, headless: bool = True, timeout: int = 30):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.playwright = None

    def start(self) -> bool:
        if sync_playwright is None:
            logger.warning("Playwright not available in this environment")
            return False
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            log_browser("Playwright launched")
            return True
        except Exception as exc:
            logger.error(f"[BROWSER] Playwright launch failed: {exc}")
            return False

    def stop(self):
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            log_browser("Playwright stopped")
        except Exception:
            pass

    def scrape(self, url: str) -> Optional[Dict[str, Optional[str]]]:
        if not self.browser:
            return None
        try:
            log_browser(f"Opening URL: {url}")
            context = self.browser.new_context(java_script_enabled=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
            page = context.new_page()
            page.set_default_navigation_timeout(self.timeout * 1000)
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.3)")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

            html = page.content()
            scraped = ContentScraper().extract_from_html(html, url)

            # scan dynamically inserted elements
            try:
                imgs = page.eval_on_selector_all("img", "els => els.map(el => ({src: el.src || el.getAttribute('data-src') || el.getAttribute('data-lazy-src') || el.getAttribute('data-original') || '', srcset: el.srcset || ''}))")
            except Exception:
                imgs = []
            try:
                videos = page.eval_on_selector_all("video", "els => els.map(el => ({src: el.currentSrc || el.src || '', sources: Array.from(el.querySelectorAll('source')).map(s => s.src)}))")
            except Exception:
                videos = []
            try:
                iframes = page.eval_on_selector_all("iframe", "els => els.map(el => el.src || el.getAttribute('data-src') || '')")
            except Exception:
                iframes = []

            context.close()

            if not scraped.get("image"):
                for item in imgs:
                    candidate = item.get("src") or ""
                    if candidate and ContentScraper()._is_valid_image(candidate):
                        scraped["image"] = candidate
                        log_browser(f"Image found via Playwright JS: {candidate}")
                        break

            if not scraped.get("video"):
                for item in videos:
                    candidate = item.get("src") or ""
                    if candidate and ContentScraper()._is_valid_video(candidate):
                        scraped["video"] = candidate
                        log_browser(f"Video found via Playwright JS (<video>): {candidate}")
                        break
                    for src in item.get("sources", []) or []:
                        if src and ContentScraper()._is_valid_video(src):
                            scraped["video"] = src
                            log_browser(f"Video found via Playwright JS (<source>): {src}")
                            break
                    if scraped.get("video"):
                        break

            if not scraped.get("video"):
                for src in iframes or []:
                    if src and ContentScraper()._is_valid_video(src):
                        scraped["video"] = src
                        log_browser(f"Video found via Playwright JS (iframe): {src}")
                        break

            return scraped
        except Exception as exc:
            logger.error(f"[BROWSER] scrape error: {exc}")
            return None


# =====================================
# Blogger HTML Template Renderer
# =====================================
class BlogHTMLRenderer:
    TEMPLATE = """
<div id="ump2026">
  <h1 class="ump-title">{POST_TITLE}</h1>

  <div class="ump-video-notice">
    <div class="ump-notice-icon">👇</div>
    <div class="ump-notice-text">
      <strong>વિડિયો જોવા માટે નીચે સ્ક્રોલ કરો</strong>
      <span>નીચે Video Player આપેલ છે</span>
    </div>
    <a href="#umpVideoPlayer" class="ump-watch-button">▶ Watch Video</a>
  </div>

  {TOP_AD}

  <div class="ump-description">{POST_DESCRIPTION}</div>

  {IMAGE_BLOCK}

  {MID_AD}

  <div class="ump-scroll-reminder">👇 <strong>થોડું નીચે સ્ક્રોલ કરો — Video Player અહીં છે</strong></div>

  <div id="umpVideoPlayer" class="ump-player-box">
    {VIDEO_BLOCK}
  </div>

  {DOWNLOAD_BLOCK}

  {BOTTOM_AD}

  <div class="ump-final-info">
    <strong>🎬 Video Player</strong>
    <p>ઉપરના Player માંથી વિડિયો ચલાવી શકો છો.</p>
  </div>
</div>
{CSS_STYLES}
"""

    CSS_STYLES = """
<style>
#ump2026 {
  width: 100%;
  max-width: 900px;
  margin: 15px auto;
  padding: 10px;
  box-sizing: border-box;
  font-family: Arial, Helvetica, sans-serif;
}
#ump2026 .ump-title {
  margin: 0 0 18px;
  padding: 0;
  font-size: 28px;
  line-height: 1.4;
  font-weight: 700;
  text-align: center;
}
#ump2026 .ump-video-notice {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 15px 0 20px;
  padding: 14px;
  background: linear-gradient(135deg, #fff7d6, #fff1a8);
  border: 2px solid #f0c400;
  border-radius: 12px;
}
#ump2026 .ump-notice-icon {
  font-size: 28px;
}
#ump2026 .ump-notice-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
#ump2026 .ump-notice-text strong { font-size: 16px; color: #222; }
#ump2026 .ump-notice-text span { font-size: 13px; color: #555; }
#ump2026 .ump-watch-button {
  display: inline-block;
  white-space: nowrap;
  padding: 10px 15px;
  background: #e60000;
  color: #fff !important;
  text-decoration: none !important;
  border-radius: 8px;
  font-size: 14px;
  font-weight: bold;
}
#ump2026 .ump-description {
  margin: 15px 0 20px;
  font-size: 16px;
  line-height: 1.75;
  color: #222;
}
#ump2026 .ump-image {
  width: 100%;
  margin: 15px 0 20px;
  text-align: center;
}
#ump2026 .ump-image img {
  width: 100%;
  max-width: 900px;
  height: auto;
  display: block;
  margin: auto;
  border-radius: 12px;
}
#ump2026 .ump-scroll-reminder {
  width: 100%;
  box-sizing: border-box;
  text-align: center;
  margin: 18px 0;
  padding: 12px;
  background: #eef7ff;
  border: 1px dashed #1683ff;
  border-radius: 8px;
  color: #1261a0;
  font-size: 14px;
}
#ump2026 .ump-player-box {
  width: 100%;
  min-height: 220px;
  background: #000;
  border-radius: 12px;
  overflow: hidden;
  margin: 20px 0;
  position: relative;
}
#ump2026 .ump-player-box video,
#ump2026 .ump-player-box iframe {
  width: 100%;
  display: block;
  border: 0;
  background: #000;
}
#ump2026 .ump-player-box video { height: auto; min-height: 200px; }
#ump2026 .ump-player-box iframe { height: 500px; }
#ump2026 .ump-download-box {
  text-align: center;
  margin: 20px 0;
}
#ump2026 .ump-download-button {
  display: inline-block;
  padding: 13px 28px;
  background: linear-gradient(135deg, #1769ff, #0044cc);
  color: #fff !important;
  text-decoration: none !important;
  border-radius: 8px;
  font-size: 16px;
  font-weight: bold;
}
#ump2026 .ump-final-info {
  margin: 20px 0;
  padding: 15px;
  background: #f5f5f5;
  border-radius: 10px;
  text-align: center;
}
#ump2026 .ump-final-info p { margin: 7px 0 0; font-size: 14px; }
@media (max-width: 600px) {
  #ump2026 { padding: 8px; }
  #ump2026 .ump-title { font-size: 22px; }
  #ump2026 .ump-description { font-size: 15px; }
  #ump2026 .ump-player-box iframe { height: 260px; }
}
html { scroll-behavior: smooth; }
</style>
"""

    @staticmethod
    def _safe_attr(value: Optional[str]) -> str:
        if not value:
            return ""
        return str(value).replace('"', '&quot;').replace("'", "&#39;")

    @staticmethod
    def _get_video_block(video_url: Optional[str]) -> str:
        if not video_url:
            return "<div class='ump-video-placeholder'>Video not available.</div>"
        lower = video_url.lower()

        if any(ext in lower for ext in [".mp4", ".webm", ".m4v", ".ogg", ".ogv"]):
            mime_type = "video/mp4"
            if ".webm" in lower:
                mime_type = "video/webm"
            elif ".ogg" in lower or ".ogv" in lower:
                mime_type = "video/ogg"
            return (
                '<video controls playsinline preload="metadata" style="max-width:100%;height:auto;display:block;background:#000;">'
                f'<source src="{BlogHTMLRenderer._safe_attr(video_url)}" type="{mime_type}">'
                '</video>'
            )

        if ".m3u8" in lower:
            return (
                '<div id="hlsContainer" style="width:100%;"></div>'
                '<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>'
                '<script>'
                '(function(){'
                'var video=document.createElement("video");video.controls=true;video.playsInline=true;video.style.width="100%";video.style.maxWidth="100%";video.style.height="auto";'
                'var container=document.getElementById("hlsContainer");if(container){container.appendChild(video);}'
                'if(window.Hls && Hls.isSupported()){var hls=new Hls();hls.loadSource(' + json.dumps(video_url) + ');hls.attachMedia(video);} else if(video.canPlayType("application/vnd.apple.mpegurl")){video.src=' + json.dumps(video_url) + ';}'
                '})();'
                '</script>'
            )

        return (
            '<iframe src="' + BlogHTMLRenderer._safe_attr(video_url) + '" '
            'allowfullscreen="true" allow="autoplay; fullscreen; picture-in-picture" '
            'frameborder="0" loading="lazy" style="width:100%;height:500px;max-width:100%;"></iframe>'
        )

    @staticmethod
    def _get_download_block(video_url: Optional[str]) -> str:
        if not video_url:
            return ""
        lower = video_url.lower()
        if any(ext in lower for ext in [".mp4", ".webm", ".m4v", ".ogg", ".ogv"]):
            return (
                '<div class="ump-download-box">'
                f'<a class="ump-download-button" href="{BlogHTMLRenderer._safe_attr(video_url)}" target="_blank" rel="noopener" download>⬇️ Download Video</a>'
                '</div>'
            )
        return ""

    @staticmethod
    def render(post_title: str, description: str, image_url: Optional[str], video_url: Optional[str]) -> str:
        clean_title = str(post_title or "Untitled Post").strip()[:200]
        clean_description = str(description or "વધુ માહિતી માટે મૂળ લેખ વાંચો.").strip()
        if len(clean_description) > 2000:
            clean_description = clean_description[:2000]

        image_block = ""
        if image_url:
            image_block = (
                '<div class="ump-image">'
                f'<img src="{BlogHTMLRenderer._safe_attr(image_url)}" alt="{BlogHTMLRenderer._safe_attr(clean_title)}" loading="eager" decoding="async" style="max-width:100%;height:auto;">'
                '</div>'
            )

        html = BlogHTMLRenderer.TEMPLATE.format(
            POST_TITLE=BlogHTMLRenderer._safe_attr(clean_title),
            POST_DESCRIPTION=clean_description,
            TOP_AD=ADSTERRA_ADS.get("banner_468x60", ""),
            IMAGE_BLOCK=image_block,
            MID_AD=ADSTERRA_ADS.get("native_banner", ""),
            VIDEO_BLOCK=BlogHTMLRenderer._get_video_block(video_url),
            DOWNLOAD_BLOCK=BlogHTMLRenderer._get_download_block(video_url),
            BOTTOM_AD=ADSTERRA_ADS.get("banner_728x90", ""),
            CSS_STYLES=BlogHTMLRenderer.CSS_STYLES,
        )
        return html


# =====================================
# Blogger API Service
# =====================================
class BloggerService:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.service = None

    def authenticate(self) -> bool:
        try:
            log_blogger("Authenticating to Google Blogger API...")
            creds = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=["https://www.googleapis.com/auth/blogger"],
            )
            creds.refresh(Request())
            self.service = build("blogger", "v3", credentials=creds)
            log_blogger("Authentication successful")
            return True
        except Exception as exc:
            logger.error(f"❌ Authentication failed: {exc}")
            return False

    def publish_post(self, blog_id: str, title: str, content: str, labels: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not self.service:
            logger.error("[BLOGGER] Service not authenticated")
            return None

        body = {"kind": "blogger#post", "title": title, "content": content}
        if labels:
            body["labels"] = labels

        try:
            log_blogger(f"Publishing: {title[:60]}")
            response = self.service.posts().insert(blogId=blog_id, body=body).execute()
            log_blogger(f"Publish successful: {response.get('url')}")
            return response
        except Exception as exc:
            err = str(exc)
            logger.error(f"[BLOGGER] Publish failed: {err}")
            return {"error": err}


# =====================================
# RSS Feed Parser
# =====================================
class RSSFeedParser:
    @staticmethod
    def parse_feeds(feed_urls: List[str], max_entries: int = 80) -> List[Dict[str, Any]]:
        all_entries: List[Dict[str, Any]] = []

        for feed_url in feed_urls:
            if not feed_url.strip():
                continue

            try:
                log_rss(f"Feed checked: {feed_url}")
                feed = feedparser.parse(feed_url)
                entries = getattr(feed, "entries", []) or []
                log_rss(f"Entries found: {len(entries)}")

                for entry in reversed(entries[:max_entries]):
                    link = getattr(entry, "link", None) or getattr(entry, "id", None)
                    if not link:
                        continue

                    item = {
                        "url": str(link).strip(),
                        "title": str(getattr(entry, "title", "") or "").strip(),
                        "published": str(getattr(entry, "published", "") or getattr(entry, "updated", "") or "").strip(),
                        "summary": str(getattr(entry, "summary", "") or "").strip(),
                        "content": "",
                        "media": {},
                    }

                    # parse content
                    content_obj = getattr(entry, "content", None)
                    if content_obj:
                        try:
                            item["content"] = str(content_obj[0].get("value", "") or "")
                        except Exception:
                            item["content"] = ""

                    # media:content, media:thumbnail, enclosure, image, video
                    media_content = getattr(entry, "media_content", None)
                    if media_content:
                        item["media"]["media_content"] = media_content

                    media_thumbnail = getattr(entry, "media_thumbnail", None)
                    if media_thumbnail:
                        item["media"]["media_thumbnail"] = media_thumbnail

                    enclosure = getattr(entry, "enclosures", None)
                    if enclosure:
                        item["media"]["enclosures"] = enclosure

                    for link_obj in getattr(entry, "links", []) or []:
                        href = link_obj.get("href")
                        rel = (link_obj.get("rel") or "").lower()
                        if rel == "enclosure" and href:
                            item["media"].setdefault("enclosures", []).append({"href": href, "type": link_obj.get("type", "")})
                        if rel == "image" and href:
                            item["media"].setdefault("images", []).append(href)

                    all_entries.append(item)
            except Exception as exc:
                logger.error(f"[RSS] Feed parse error: {exc}")
                continue

        log_rss(f"Total collected entries: {len(all_entries)}")
        return all_entries


# =====================================
# Main Automation
# =====================================
class RSSBloggerAutomation:
    def __init__(self):
        self.posted_urls = PostedURLsManager()
        self.pending = PendingQueue()
        self.scraper = ContentScraper()
        self.browser_scraper = BrowserScraper()
        self.blogger = BloggerService(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)

    def _merge_rss_and_page(self, entry: Dict[str, Any], scraped: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
        data: Dict[str, Optional[str]] = {
            "title": (scraped or {}).get("title") or entry.get("title") or "Untitled Post",
            "description": (scraped or {}).get("description") or entry.get("summary") or entry.get("content") or "વધુ માહિતી માટે મૂળ લેખ વાંચો.",
            "image": (scraped or {}).get("image"),
            "video": (scraped or {}).get("video"),
        }

        # try RSS media metadata when webpage didn't provide them
        if not data.get("image"):
            media = entry.get("media", {}) or {}
            for key in ["media_thumbnail", "enclosures", "images"]:
                value = media.get(key)
                if not value:
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            url = item.get("url") or item.get("href") or item.get("src")
                            if url and ContentScraper()._is_valid_image(url):
                                data["image"] = str(url)
                                break
                        else:
                            url = str(item)
                            if ContentScraper()._is_valid_image(url):
                                data["image"] = url
                                break
                elif isinstance(value, str):
                    if ContentScraper()._is_valid_image(value):
                        data["image"] = value
            if not data.get("image"):
                for key in ["media_content", "media_thumbnail"]:
                    value = media.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                url = item.get("url") or item.get("href")
                                if url and ContentScraper()._is_valid_image(str(url)):
                                    data["image"] = str(url)
                                    break

        if not data.get("video"):
            media = entry.get("media", {}) or {}
            for key in ["media_content", "enclosures"]:
                value = media.get(key)
                if not value:
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            url = item.get("url") or item.get("href")
                            if url and ContentScraper()._is_valid_video(url):
                                data["video"] = str(url)
                                break
                        else:
                            url = str(item)
                            if ContentScraper()._is_valid_video(url):
                                data["video"] = url
                                break

        return data

    def _should_publish(self, title: str, video: Optional[str], description: str) -> bool:
        if not title or not str(title).strip():
            log_validation("Title not found")
            return False
        if not video:
            log_validation("Video not found")
            return False
        # description is optional but we keep fallback text.
        return True

    def run(self) -> bool:
        logger.info("=" * 100)
        logger.info("🚀 RSS to Blogger Automation")
        logger.info("=" * 100)
        logger.info(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, RSS_FEED_URLS]):
            logger.error("Incomplete environment variables. Required: BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, RSS_FEED_URLS")
            return False

        if not self.blogger.authenticate():
            return False

        browser_ready = self.browser_scraper.start()

        pending_batch = self.pending.pop_batch(MAX_POSTS_PER_RUN)
        if pending_batch:
            log_queue(f"Resuming {len(pending_batch)} pending posts")

        feed_urls = [u.strip() for u in RSS_FEED_URLS.split(",") if u.strip()]
        all_entries = RSSFeedParser.parse_feeds(feed_urls, max_entries=80)

        queue_candidates: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for entry in pending_batch:
            url = normalize_url(str(entry.get("url") or ""))
            if url and url not in seen_urls:
                queue_candidates.append(entry)
                seen_urls.add(url)

        for entry in all_entries:
            url = normalize_url(str(entry.get("url") or ""))
            if not url:
                continue
            if self.posted_urls.is_posted(url):
                continue
            if any(normalize_url(str(existing.get("url", "") or "")) == url for existing in self.pending.queue):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            queue_candidates.append(entry)

        queue_candidates = queue_candidates[:MAX_POSTS_PER_RUN]
        log_rss(f"Queued for processing: {len(queue_candidates)} posts")

        published_count = 0
        failed_count = 0

        for idx, entry in enumerate(queue_candidates, 1):
            entry_url = normalize_url(str(entry.get("url") or ""))
            title_hint = str(entry.get("title") or "Untitled Post").strip()
            log_rss(f"[{idx}/{len(queue_candidates)}] Processing: {title_hint[:70]} | {entry_url}")

            scraped = None
            if browser_ready:
                scraped = self.browser_scraper.scrape(entry_url)
                if scraped:
                    log_scraper("Browser scrape succeeded")
            if not scraped:
                html = self.scraper.fetch(entry_url)
                if html:
                    scraped = self.scraper.extract_from_html(html, entry_url)
                    log_scraper("HTML fallback scrape succeeded")

            merged = self._merge_rss_and_page(entry, scraped)
            title = str(merged.get("title") or title_hint or urlparse(entry_url).netloc).strip()
            description = str(merged.get("description") or "વધુ માહિતી માટે મૂળ લેખ વાંચો.").strip()
            image = merged.get("image")
            video = merged.get("video")

            log_scraper(f"Result: title={'yes' if title else 'no'}, description={'yes' if description else 'no'}, image={'yes' if image else 'no'}, video={'yes' if video else 'no'}")

            if not self._should_publish(title, video, description):
                log_validation("Video not found or title missing - moving to pending")
                self.pending.push({"url": entry_url, "title": title_hint, "reason": "Missing title/video", "first_seen": datetime.utcnow().isoformat()})
                failed_count += 1
                continue

            log_validation("All required data available for publishing")
            html_content = BlogHTMLRenderer.render(title, description, image, video)

            resp = self.blogger.publish_post(
                BLOG_ID,
                title,
                html_content,
                labels=[AUTO_POST_LABEL, "Automated", "RSS Feed", "Custom Template"],
            )

            if resp is None:
                self.pending.push({"url": entry_url, "title": title_hint, "reason": "Unknown publish failure", "first_seen": datetime.utcnow().isoformat()})
                failed_count += 1
                continue

            if isinstance(resp, dict) and resp.get("error"):
                err = str(resp.get("error", "")).lower()
                if any(token in err for token in ["429", "ratelimit", "quota", "dailylimit", "userratelimit"]):
                    log_blogger("Quota/rate limit reached - stopping publishing safely")
                    self.pending.push({"url": entry_url, "title": title_hint, "reason": "Rate limit hit", "first_seen": datetime.utcnow().isoformat()})
                    # Preserve remaining items in queue
                    for remaining in queue_candidates[idx:]:
                        self.pending.push({"url": normalize_url(str(remaining.get("url") or "")), "title": str(remaining.get("title") or "").strip(), "reason": "Deferred due to rate limit", "first_seen": datetime.utcnow().isoformat()})
                    self.pending.save()
                    self.posted_urls.save()
                    self.browser_scraper.stop()
                    return True

                self.pending.push({"url": entry_url, "title": title_hint, "reason": f"Publish error: {resp.get('error')}", "first_seen": datetime.utcnow().isoformat()})
                failed_count += 1
                continue

            post_id = resp.get("id")
            post_url = resp.get("url")
            if post_id and post_url:
                self.posted_urls.add(entry_url)
                self.pending.remove(entry_url)
                self.posted_urls.save()
                self.pending.save()
                published_count += 1
                log_queue(f"Post marked as completed: {entry_url}")
                if idx < len(queue_candidates):
                    time.sleep(3)
            else:
                self.pending.push({"url": entry_url, "title": title_hint, "reason": "Unexpected Blogger response", "first_seen": datetime.utcnow().isoformat()})
                failed_count += 1

        if browser_ready:
            self.browser_scraper.stop()

        self.pending.save()
        self.posted_urls.save()

        logger.info("\n" + "=" * 100)
        logger.info("📊 EXECUTION SUMMARY")
        logger.info("=" * 100)
        logger.info(f"✅ Published: {published_count} posts")
        logger.info(f"❌ Failed: {failed_count} posts")
        logger.info(f"📊 Total Posts (All-Time): {len(self.posted_urls.urls)}")
        logger.info("=" * 100)
        return True


# =====================================
# Main Entry Point
# =====================================
def main():
    try:
        automation = RSSBloggerAutomation()
        success = automation.run()
        sys.exit(0 if success else 1)
    except Exception as exc:
        logger.error(f"❌ Unexpected error: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
