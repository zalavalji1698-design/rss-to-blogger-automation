#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS to Blogger Automation with Content Extraction & Ads
- RSS feeds से पोस्ट URLs निकालता है
- हर post को scrape करके full content extract करता है
- Images, Videos, Thumbnails extract करता है (सभी प्रकार: JPEG, PNG, MP4, iframe)
- Adsterra ads को content के बीच add करता है
- Blogger पर rich HTML के साथ publish करता है
- API Rate Limiting (429 errors) को exponential backoff से handle करता है
"""

import os
import json
import sys
import logging
import requests
import time
from typing import List, Set, Optional, Dict
from datetime import datetime
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import feedparser
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

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

# =====================================
# Configuration
# =====================================
BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "").strip()
CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
RSS_FEED_URLS = os.environ.get("RSS_FEED_URLS", "").strip()

POSTED_FILE = "posted_urls.json"
AUTO_POST_LABEL = "RSS Auto Post"

# =====================================
# Adsterra Ads Configuration
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
# Posted URLs Manager
# =====================================
class PostedURLsManager:
    """पहले से post किए गए URLs को manage करता है"""

    def __init__(self, file_path: str = POSTED_FILE):
        self.file_path = file_path
        self.urls = self._load()

    def _load(self) -> Set[str]:
        try:
            if not os.path.exists(self.file_path):
                return set()

            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                return set()
        except Exception:
            return set()

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(sorted(list(self.urls)), f, ensure_ascii=False, indent=2)
            logger.info(f"💾 Posted URLs saved ({len(self.urls)} total)")
        except Exception as e:
            logger.error(f"❌ Save error: {e}")

    def is_posted(self, url: str) -> bool:
        return url in self.urls

    def add(self, url: str):
        self.urls.add(url)

# =====================================
# Web Content Scraper
# =====================================
class ContentScraper:
    """RSS post URLs से content को scrape करता है with media extraction"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Session with retry strategy और exponential backoff"""
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "HEAD"],
            backoff_factor=1
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        return session

    def scrape_post(self, url: str) -> Optional[Dict]:
        """
        किसी URL से post का data scrape करता है including images and videos
        """
        try:
            logger.info(f"🕷️  Scraping: {url}")

            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = 'utf-8'

            soup = BeautifulSoup(response.content, 'html.parser')

            title = self._extract_title(soup, url)
            content = self._extract_content(soup)
            images = self._extract_images(soup, url)
            videos = self._extract_videos(soup, url)
            thumbnail = self._extract_thumbnail(soup, images)

            logger.info(f"   ✅ Extracted: {len(images)} images, {len(videos)} videos")

            return {
                'title': title,
                'content': content,
                'images': images,
                'videos': videos,
                'thumbnail': thumbnail,
                'original_url': url
            }

        except Exception as e:
            logger.error(f"   ❌ Scrape error: {e}")
            return None

    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        """Page से title निकालता है (multiple strategies)"""
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
            if title:
                return title

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content']

        twitter_title = soup.find('meta', attrs={'name': 'twitter:title'})
        if twitter_title and twitter_title.get('content'):
            return twitter_title['content']

        title_tag = soup.find('title')
        if title_tag:
            return title_tag.get_text(strip=True)

        return urlparse(url).netloc

    def _extract_content(self, soup: BeautifulSoup) -> str:
        """Page से main content निकालता है"""
        main_content = None

        article = soup.find('article')
        if article:
            main_content = article

        if not main_content:
            main = soup.find('main')
            if main:
                main_content = main

        if not main_content:
            for class_name in ['content', 'post-content', 'entry-content', 'article-content', 'post', 'article']:
                content_div = soup.find('div', class_=class_name)
                if content_div:
                    main_content = content_div
                    break

        if not main_content:
            main_content = soup.find('body')

        if not main_content:
            return "<p>No content found</p>"

        content_html = ""
        for element in main_content.find_all(['p', 'h2', 'h3', 'h4', 'blockquote', 'ul', 'ol', 'div']):
            if element.name in ['script', 'style']:
                continue

            text = element.get_text(strip=True)
            if text and len(text) > 10:
                if element.name == 'div' and len(text) > 500:
                    continue
                content_html += str(element) + "\n"

        return content_html if content_html else "<p>No content found</p>"

    def _extract_images(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Page से सभी प्रकार की images निकालता है (JPEG, PNG, WebP, etc.)"""
        images = []
        seen_urls = set()

        # <img> tags से
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                full_url = urljoin(base_url, src)
                if full_url not in seen_urls and self._is_valid_image(full_url):
                    images.append(full_url)
                    seen_urls.add(full_url)

        # Open Graph image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            og_url = og_image['content']
            if og_url not in seen_urls:
                images.insert(0, og_url)
                seen_urls.add(og_url)

        # Twitter Card image
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            twitter_url = twitter_image['content']
            if twitter_url not in seen_urls:
                images.append(twitter_url)
                seen_urls.add(twitter_url)

        # <picture> tags से
        for picture in soup.find_all('picture'):
            for source in picture.find_all('source'):
                srcset = source.get('srcset')
                if srcset:
                    first_url = srcset.split(',')[0].split()[0]
                    full_url = urljoin(base_url, first_url)
                    if full_url not in seen_urls and self._is_valid_image(full_url):
                        images.append(full_url)
                        seen_urls.add(full_url)

        return images[:15]

    def _extract_videos(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Page से videos निकालता है (MP4, iframe, YouTube, Vimeo, etc.)"""
        videos = []
        seen_urls = set()

        # Iframe videos (YouTube, Vimeo, Dailymotion, etc.)
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if src and any(platform in src for platform in ['youtube', 'youtu.be', 'vimeo', 'dailymotion', 'rumble', 'odysee']):
                if src not in seen_urls:
                    videos.append(src)
                    seen_urls.add(src)

        # <video> tags
        for video_tag in soup.find_all('video'):
            src = video_tag.get('src')
            if src:
                full_url = urljoin(base_url, src)
                if full_url not in seen_urls:
                    videos.append(full_url)
                    seen_urls.add(full_url)

            # <source> tags inside <video>
            for source in video_tag.find_all('source'):
                src = source.get('src')
                video_type = source.get('type', '')
                if src and ('video/mp4' in video_type or 'video' in video_type):
                    full_url = urljoin(base_url, src)
                    if full_url not in seen_urls:
                        videos.append(full_url)
                        seen_urls.add(full_url)

        # Data attributes से videos
        for elem in soup.find_all(['div', 'a'], class_=lambda x: x and 'video' in x.lower()):
            for attr in ['data-video-url', 'data-src', 'data-video']:
                video_url = elem.get(attr)
                if video_url and video_url not in seen_urls:
                    videos.append(video_url)
                    seen_urls.add(video_url)

        return videos[:10]

    def _extract_thumbnail(self, soup: BeautifulSoup, images: List[str]) -> Optional[str]:
        """Thumbnail image निकालता है"""
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']

        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            return twitter_image['content']

        if images:
            return images[0]

        return None

    def _is_valid_image(self, url: str) -> bool:
        """Check करता है कि URL एक valid image है"""
        if not url:
            return False

        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']
        invalid_patterns = ['ads', 'tracking', 'pixel', 'spacer', '1x1', 'favicon']

        url_lower = url.lower()
        if any(pattern in url_lower for pattern in invalid_patterns):
            return False

        if any(url_lower.endswith(ext) for ext in valid_extensions):
            return True

        if any(cdn in url_lower for cdn in ['cloudinary', 'imgix', 'imageserve', 'pbs.twimg', 'imgur']):
            return True

        return False

# =====================================
# HTML Content Builder with Ad Injection
# =====================================
class HTMLContentBuilder:
    """Scraped content को rich HTML में convert करता है और ads inject करता है"""

    @staticmethod
    def build_post_html(scraped_data: Dict) -> str:
        """Scraped data को Blogger-friendly HTML में convert करता है with images, videos, and ads"""
        html = ""

        # Thumbnail
        if scraped_data.get('thumbnail'):
            html += f"""
<div style="text-align: center; margin-bottom: 25px;">
    <img src="{scraped_data['thumbnail']}"
         alt="{scraped_data['title']}"
         style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
</div>
"""

        # Main content with ads injected
        main_content = scraped_data.get('content', "")
        html += HTMLContentBuilder._inject_ads_in_content(main_content)

        # Image Gallery
        if scraped_data.get('images'):
            html += "\n<div style='margin-top: 30px; padding-top: 20px; border-top: 2px solid #e0e0e0;'>"
            html += "<h3 style='color: #333; font-size: 1.3em; margin-bottom: 15px;'>📸 Image Gallery</h3>"
            html += '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin: 15px 0;">'

            for idx, img_url in enumerate(scraped_data['images'][:12], 1):
                html += f"""
<div style="overflow: hidden; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
    <img src="{img_url}" alt="Image {idx}" style="max-width: 100%; height: auto; display: block;">
</div>
"""
            html += '</div>'
            html += "</div>"
            html += ADSTERRA_ADS['banner_728x90']

        # Videos
        if scraped_data.get('videos'):
            html += "\n<div style='margin-top: 30px; padding-top: 20px; border-top: 2px solid #e0e0e0;'>"
            html += "<h3 style='color: #333; font-size: 1.3em; margin-bottom: 15px;'>🎥 Videos</h3>"

            for video_url in scraped_data['videos'][:5]:
                if 'youtube' in video_url or 'youtu.be' in video_url:
                    video_id = HTMLContentBuilder._extract_youtube_id(video_url)
                    if video_id:
                        html += f"""
<div style="margin-bottom: 20px;">
    <div style="position: relative; width: 100%; padding-bottom: 56.25%; border-radius: 8px; overflow: hidden;">
        <iframe
            width="100%"
            height="100%"
            src="https://www.youtube.com/embed/{video_id}"
            style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowfullscreen>
        </iframe>
    </div>
</div>
"""
                elif 'vimeo' in video_url:
                    html += f"""
<div style="margin-bottom: 20px;">
    <div style="position: relative; width: 100%; padding-bottom: 56.25%; border-radius: 8px; overflow: hidden;">
        <iframe
            src="{video_url}"
            width="100%"
            height="100%"
            style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
            allow="autoplay; fullscreen; picture-in-picture"
            allowfullscreen>
        </iframe>
    </div>
</div>
"""
                elif video_url.endswith('.mp4'):
                    html += f"""
<div style="margin-bottom: 20px;">
    <video width="100%" height="auto" controls style="border-radius: 8px;">
        <source src="{video_url}" type="video/mp4">
        Your browser does not support the video tag.
    </video>
</div>
"""
                else:
                    html += f"""
<div style="margin-bottom: 20px;">
    <div style="position: relative; width: 100%; padding-bottom: 56.25%; border-radius: 8px; overflow: hidden;">
        <iframe
            src="{video_url}"
            style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
            allowfullscreen>
        </iframe>
    </div>
</div>
"""

            html += "</div>"
            html += ADSTERRA_ADS['banner_468x60']

        # Final native ad
        html += ADSTERRA_ADS['native_banner']

        # Original source link
        html += f"""
<div style="margin-top: 30px; padding: 15px; border-top: 2px solid #e0e0e0; background-color: #f5f5f5; border-radius: 5px;">
    <p style="margin: 0; color: #666;">
        <strong>📌 Original Source:</strong>
        <a href="{scraped_data['original_url']}" target="_blank" style="color: #0066cc; text-decoration: none;">
            Read Full Article
        </a>
    </p>
</div>
"""

        return html

    @staticmethod
    def _inject_ads_in_content(html: str) -> str:
        """HTML के बीच-बीच में ads inject करता है (हर 3 paragraphs के बाद)"""
        if not html:
            return html

        paragraphs = html.split('</p>')

        if len(paragraphs) < 4:
            return html

        result = ""
        ad_count = 0

        for i, para in enumerate(paragraphs[:-1], 1):
            result += para + '</p>'

            if i % 3 == 0 and i < len(paragraphs) - 1:
                if ad_count % 2 == 0:
                    result += ADSTERRA_ADS['banner_728x90']
                else:
                    result += ADSTERRA_ADS['banner_468x60']
                ad_count += 1

                if ad_count >= 3:
                    break

        result += paragraphs[-1]
        return result

    @staticmethod
    def _extract_youtube_id(url: str) -> Optional[str]:
        """YouTube URL से video ID निकालता है"""
        if 'youtube.com/watch' in url:
            try:
                return url.split('v=')[1].split('&')[0]
            except IndexError:
                return None
        elif 'youtu.be/' in url:
            try:
                return url.split('youtu.be/')[1].split('?')[0]
            except IndexError:
                return None
        elif 'youtube.com/embed/' in url:
            try:
                return url.split('embed/')[1].split('?')[0]
            except IndexError:
                return None

        return None

# =====================================
# Blogger API Service with Rate Limit Handling
# =====================================
class BloggerService:
    """Google Blogger API के साथ interact करता है with exponential backoff for 429 errors"""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.service = None

    def authenticate(self) -> bool:
        """Google OAuth से authenticate करता है"""
        try:
            logger.info("🔐 Authenticating with Google Blogger API...")

            creds = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=["https://www.googleapis.com/auth/blogger"]
            )

            creds.refresh(Request())
            self.service = build("blogger", "v3", credentials=creds)
            logger.info("✅ Authentication successful")
            return True

        except Exception as e:
            logger.error(f"❌ Authentication failed: {e}")
            return False

    def publish_post(self, blog_id: str, title: str, content: str, labels: List[str] = None) -> Optional[str]:
        """Blogger पर post publish करता है with exponential backoff for 429 rate limiting errors"""
        if not self.service:
            logger.error("❌ Service not authenticated")
            return None

        max_retries = 4
        retry_count = 0
        base_wait_time = 2

        while retry_count < max_retries:
            try:
                body = {
                    "kind": "blogger#post",
                    "title": title,
                    "content": content
                }

                if labels:
                    body["labels"] = labels

                logger.info(f"📤 Publishing post (attempt {retry_count + 1}/{max_retries})...")
                request = self.service.posts().insert(blogId=blog_id, body=body)
                response = request.execute()

                post_id = response.get("id")
                post_url = response.get("url")

                logger.info(f"✅ Published: {title[:60]}")
                logger.info(f"   🔗 URL: {post_url}")
                logger.info(f"   🏷️  Labels: {', '.join(labels) if labels else 'None'}")

                return post_id

            except Exception as e:
                error_str = str(e)

                # 429 Rate Limiting या Quota Exceeded को handle करना
                if "429" in error_str or "quota" in error_str.lower() or "exhausted" in error_str.lower():
                    retry_count += 1

                    if retry_count < max_retries:
                        wait_time = base_wait_time ** retry_count
                        logger.warning(f"⏸️  RATE LIMITED (429 / Quota Exceeded)")
                        logger.warning(f"   ⏳ Waiting {wait_time}s before retry {retry_count}/{max_retries-1}")
                        logger.warning(f"   💡 Consider reducing RSS feeds or increasing cron interval")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"❌ Max retries ({max_retries}) exceeded for rate limiting")
                        logger.error(f"❌ Publish failed: {title}")
                        return None

                # अन्य errors
                logger.error(f"❌ Publish failed: {title}")
                logger.error(f"   Error: {e}")
                return None

        logger.error(f"❌ Publish failed after {max_retries} retries: {title}")
        return None

# =====================================
# RSS Feed Parser
# =====================================
class RSSFeedParser:
    """RSS feeds को parse करता है"""

    @staticmethod
    def parse_feeds(feed_urls: List[str], max_entries: int = 20) -> List[Dict]:
        """RSS feeds को parse करके entries return करता है"""
        all_entries = []

        for feed_url in feed_urls:
            if not feed_url.strip():
                continue

            try:
                logger.info(f"📡 Reading RSS feed: {feed_url}")

                feed = feedparser.parse(feed_url)
                entries = getattr(feed, "entries", [])

                if not entries:
                    logger.warning(f"   ⚠️ No entries found")
                    continue

                logger.info(f"   📝 Found {len(entries)} entries")

                for entry in reversed(entries[:max_entries]):
                    entry_link = getattr(entry, "link", None)
                    if not entry_link:
                        continue

                    entry_title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
                    entry_published = getattr(entry, "published", "")

                    all_entries.append({
                        "url": entry_link,
                        "title": entry_title,
                        "published": entry_published,
                        "feed_url": feed_url
                    })

            except Exception as e:
                logger.error(f"   ❌ Error parsing feed: {e}")
                continue

        logger.info(f"🎯 Total {len(all_entries)} entries collected from all feeds")
        return all_entries

    @staticmethod
    def is_today_post(published_date: str) -> bool:
        """Check करता है कि post आज का है या नहीं"""
        if not published_date:
            return True

        try:
            from email.utils import parsedate_to_datetime
            published = parsedate_to_datetime(published_date)
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            return published.date() == today.date()
        except Exception:
            return True

# =====================================
# Main Automation
# =====================================
class RSSBloggerAutomation:
    """Main automation orchestrator"""

    def __init__(self):
        self.posted_urls_manager = PostedURLsManager()
        self.blogger_service = BloggerService(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
        self.scraper = ContentScraper()

    def run(self) -> bool:
        """Main automation flow"""

        logger.info("=" * 100)
        logger.info("🚀 RSS to Blogger Automation with Content Extraction, Media & Ads")
        logger.info("=" * 100)
        logger.info(f"⏰ Execution Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Step 1: Validate config
        if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, RSS_FEED_URLS]):
            logger.error("❌ Missing required environment variables")
            logger.error("   Required: BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, RSS_FEED_URLS")
            return False

        # Step 2: Authenticate
        if not self.blogger_service.authenticate():
            return False

        # Step 3: Parse RSS feeds
        feed_urls = [url.strip() for url in RSS_FEED_URLS.split(",") if url.strip()]
        logger.info(f"📡 Reading from {len(feed_urls)} feed(s)")
        all_entries = RSSFeedParser.parse_feeds(feed_urls, max_entries=30)

        if not all_entries:
            logger.warning("⚠️ No RSS entries found")
            return True

        # Step 4: Separate today's posts
        todays_posts = [e for e in all_entries if RSSFeedParser.is_today_post(e.get("published", ""))]
        other_posts = [e for e in all_entries if not RSSFeedParser.is_today_post(e.get("published", ""))]
        to_process = todays_posts + other_posts

        logger.info(f"📅 Today's Posts: {len(todays_posts)}")
        logger.info(f"📆 Other Posts: {len(other_posts)}")

        # Step 5: Process posts
        logger.info("\n📝 Processing & Publishing Posts with Media")
        logger.info("-" * 100)

        published_count = 0
        skipped_count = 0
        failed_count = 0

        for idx, entry in enumerate(to_process, 1):
            entry_url = entry["url"]
            entry_title = entry["title"]

            logger.info(f"\n[{idx}/{len(to_process)}] Processing: {entry_title[:60]}...")

            # Check if already posted
            if self.posted_urls_manager.is_posted(entry_url):
                logger.info("   ⏭️  SKIPPED (already posted)")
                skipped_count += 1
                continue

            # Scrape content with media
            logger.info("   🕷️  Scraping content with images and videos...")
            scraped_data = self.scraper.scrape_post(entry_url)

            if not scraped_data:
                logger.warning("   ⚠️ Failed to scrape, using fallback content")
                html_content = f"""
<div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #e0e0e0; border-radius: 5px;">
    <p>📌 <strong>Full content available at:</strong> <a href="{entry_url}" target="_blank" style="color: #0066cc;">Read Original Post</a></p>
</div>
"""
            else:
                logger.info(f"      📊 Media found: {len(scraped_data.get('images', []))} images, {len(scraped_data.get('videos', []))} videos")
                html_content = HTMLContentBuilder.build_post_html(scraped_data)

            # Publish
            logger.info("   📤 Publishing to Blogger...")
            labels = [AUTO_POST_LABEL, "Automated", "Curated", "Media"]

            post_id = self.blogger_service.publish_post(
                BLOG_ID,
                entry_title,
                html_content,
                labels=labels
            )

            if post_id:
                self.posted_urls_manager.add(entry_url)
                published_count += 1

                # Rate limiting delay
                if idx < len(to_process):
                    logger.info("   ⏳ Waiting 3 seconds before next post...")
                    time.sleep(3)
            else:
                failed_count += 1
                logger.warning("   ⚠️ Failed to publish")

        # Step 6: Save state
        self.posted_urls_manager.save()

        # Summary
        logger.info("\n" + "=" * 100)
        logger.info("📊 EXECUTION SUMMARY")
        logger.info("=" * 100)
        logger.info(f"✅ Published: {published_count} posts")
        logger.info(f"⏭️  Skipped: {skipped_count} posts (already published)")
        logger.info(f"❌ Failed: {failed_count} posts")
        logger.info(f"📊 Total Posts (All-Time): {len(self.posted_urls_manager.urls)}")
        logger.info("=" * 100)

        if published_count > 0 or failed_count == 0:
            logger.info("✅ Automation completed successfully!")
        else:
            logger.warning("⚠️ Automation completed with issues!")

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

    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
