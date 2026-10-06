#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS to Blogger Automation with Content Extraction & Ads
- RSS feeds से पोस्ट URLs निकालता है
- हर post को scrape करके full content extract करता है
- Images, Videos, Thumbnails extract करता है
- Adsterra ads को content के बीच add करता है
- Blogger पर rich HTML के साथ publish करता है
"""

import os
import json
import sys
import logging
import requests
import time
from typing import List, Set, Optional, Dict, Tuple
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
    "banner_468x60": """<div style="text-align: center; margin: 15px 0;">
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
    
    "banner_728x90": """<div style="text-align: center; margin: 15px 0;">
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
    
    "native_banner": """<div style="margin: 15px 0;">
<script async="async" data-cfasync="false" src="https://sponsorinserttimeout.com/98ba824931c676f939dd9b860ee5df26/invoke.js"></script>
<div id="container-98ba824931c676f939dd9b860ee5df26"></div>
</div>""",
    
    "social_bar": """<div style="margin: 15px 0;">
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
    """RSS post URLs से content को scrape करता है"""
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Session बनाता है with retry strategy"""
        session = requests.Session()
        
        # Retry strategy with exponential backoff
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        return session
    
    def scrape_post(self, url: str) -> Optional[Dict]:
        """
        किसी URL से post का data scrape करता है
        
        Returns:
            {
                'title': str,
                'content': str (HTML),
                'images': [urls],
                'videos': [urls],
                'thumbnail': str (URL)
            }
        """
        try:
            logger.info(f"🕷️  Scraping: {url}")
            
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = 'utf-8'
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Title निकालना
            title = self._extract_title(soup, url)
            
            # Content निकालना
            content = self._extract_content(soup)
            
            # Images निकालना
            images = self._extract_images(soup, url)
            
            # Videos निकालना
            videos = self._extract_videos(soup)
            
            # Thumbnail निकालना
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
        """Page से title निकालता है"""
        # Method 1: <h1> tag
        h1 = soup.find('h1')
        if h1:
            return h1.get_text(strip=True)
        
        # Method 2: <meta property="og:title">
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content']
        
        # Method 3: <title> tag
        title_tag = soup.find('title')
        if title_tag:
            return title_tag.get_text(strip=True)
        
        # Fallback
        return urlparse(url).netloc
    
    def _extract_content(self, soup: BeautifulSoup) -> str:
        """Page से main content निकालता है"""
        # Main content को identify करने की कोशिश करें
        main_content = None
        
        # Article tag
        article = soup.find('article')
        if article:
            main_content = article
        
        # Main tag
        main = soup.find('main')
        if main:
            main_content = main
        
        # Content div
        if not main_content:
            content_div = soup.find('div', class_=['content', 'post-content', 'entry-content', 'article-content'])
            if content_div:
                main_content = content_div
        
        if not main_content:
            main_content = soup.find('body')
        
        # Paragraphs निकालना
        content_html = ""
        for p in main_content.find_all(['p', 'h2', 'h3', 'h4', 'blockquote']):
            text = p.get_text(strip=True)
            if text and len(text) > 10:
                content_html += str(p) + "\n"
        
        return content_html if content_html else "<p>No content found</p>"
    
    def _extract_images(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Page से सभी images निकालता है"""
        images = []
        
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src:
                full_url = urljoin(base_url, src)
                if full_url not in images and self._is_valid_image(full_url):
                    images.append(full_url)
        
        # Open Graph image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            og_url = og_image['content']
            if og_url not in images:
                images.insert(0, og_url)
        
        return images[:10]  # Maximum 10 images
    
    def _extract_videos(self, soup: BeautifulSoup) -> List[str]:
        """Page से videos निकालता है (YouTube, Vimeo etc)"""
        videos = []
        
        # Iframe videos
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if any(x in src for x in ['youtube', 'vimeo', 'dailymotion']):
                videos.append(src)
        
        # Video tags
        for video in soup.find_all('video'):
            src = video.get('src')
            if src:
                videos.append(src)
        
        return videos[:5]  # Maximum 5 videos
    
    def _extract_thumbnail(self, soup: BeautifulSoup, images: List[str]) -> Optional[str]:
        """Thumbnail image निकालता है"""
        # Open Graph image (best for thumbnail)
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
        
        # First suitable image
        if images:
            return images[0]
        
        return None
    
    def _is_valid_image(self, url: str) -> bool:
        """Check करता है कि URL एक valid image है"""
        invalid_patterns = ['ads', 'tracking', 'pixel', 'spacer', '1x1']
        return not any(pattern in url.lower() for pattern in invalid_patterns)

# =====================================
# HTML Content Builder
# =====================================
class HTMLContentBuilder:
    """Scraped content को rich HTML में convert करता है"""
    
    @staticmethod
    def build_post_html(scraped_data: Dict) -> str:
        """
        Scraped data को Blogger-friendly HTML में convert करता है
        """
        html = ""
        
        # Thumbnail (अगर मिला तो)
        if scraped_data.get('thumbnail'):
            html += f"""
<div style="text-align: center; margin-bottom: 20px;">
    <img src="{scraped_data['thumbnail']}" 
         alt="{scraped_data['title']}" 
         style="max-width: 100%; height: auto; border-radius: 8px;">
</div>
"""
        
        # Main content
        html += scraped_data.get('content', "")
        
        # Images को अगर content में नहीं हैं तो add करना
        if scraped_data.get('images'):
            html += "\n<h3 style='margin-top: 30px;'>📸 Images</h3>"
            html += '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin: 15px 0;">'
            for img_url in scraped_data['images'][:6]:
                html += f"""
<div style="overflow: hidden; border-radius: 8px;">
    <img src="{img_url}" alt="Image" style="max-width: 100%; height: auto; display: block;">
</div>
"""
            html += '</div>'
        
        # Videos को add करना
        if scraped_data.get('videos'):
            html += "\n<h3 style='margin-top: 30px;'>🎥 Videos</h3>"
            for video_url in scraped_data['videos'][:3]:
                html += f"""
<div style="position: relative; width: 100%; padding-bottom: 56.25%; margin-bottom: 20px; border-radius: 8px; overflow: hidden;">
    <iframe 
        src="{video_url}" 
        style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none;"
        allowfullscreen>
    </iframe>
</div>
"""
        
        # Ads add करना (middle में)
        html = HTMLContentBuilder._insert_ads(html)
        
        # Original source link
        html += f"""
<div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
    <p><em>📌 Source: <a href="{scraped_data['original_url']}" target="_blank">Read Original Post</a></em></p>
</div>
"""
        
        return html
    
    @staticmethod
    def _insert_ads(html: str) -> str:
        """HTML के बीच-बीच में ads insert करता है"""
        # Content को paragraphs में split करना
        paragraphs = html.split('</p>')
        
        if len(paragraphs) < 3:
            return html  # बहुत छोटा content है
        
        # हर 3-4 paragraphs के बाद ad add करना
        result = ""
        for i, para in enumerate(paragraphs[:-1], 1):
            result += para + '</p>'
            
            # हर 3 paragraphs के बाद
            if i % 3 == 0 and i != len(paragraphs) - 1:
                # Alternate ads
                if (i // 3) % 2 == 1:
                    result += ADSTERRA_ADS['banner_468x60']
                else:
                    result += ADSTERRA_ADS['banner_728x90']
        
        # Last paragraph
        result += paragraphs[-1]
        
        # End में final ad
        result += ADSTERRA_ADS['native_banner']
        
        return result

# =====================================
# Blogger API Service
# =====================================
class BloggerService:
    """Google Blogger API के साथ interact करता है"""
    
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
        """Blogger पर post publish करता है with exponential backoff"""
        if not self.service:
            logger.error("❌ Service not authenticated")
            return None
        
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                body = {
                    "kind": "blogger#post",
                    "title": title,
                    "content": content
                }
                
                if labels:
                    body["labels"] = labels
                
                request = self.service.posts().insert(blogId=blog_id, body=body)
                response = request.execute()
                
                post_id = response.get("id")
                post_url = response.get("url")
                
                logger.info(f"✅ Published: {title}")
                logger.info(f"   🔗 URL: {post_url}")
                logger.info(f"   🏷️  Labels: {', '.join(labels) if labels else 'None'}")
                
                return post_id
            
            except Exception as e:
                error_str = str(e)
                
                # 429 error को handle करें
                if "429" in error_str or "quota" in error_str.lower():
                    retry_count += 1
                    if retry_count < max_retries:
                        wait_time = 2 ** retry_count  # 2, 4, 8 seconds
                        logger.warning(f"⏸️  Rate limited (429). Retrying in {wait_time}s... ({retry_count}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                
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
                logger.error(f"   ❌ Error: {e}")
                continue
        
        logger.info(f"🎯 Total {len(all_entries)} entries collected")
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
        
        logger.info("=" * 80)
        logger.info("🚀 RSS to Blogger Automation with Content Extraction")
        logger.info("=" * 80)
        logger.info(f"⏰ Execution Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Step 1: Validate config
        if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, RSS_FEED_URLS]):
            logger.error("❌ Missing required environment variables")
            return False
        
        # Step 2: Authenticate
        if not self.blogger_service.authenticate():
            return False
        
        # Step 3: Parse RSS feeds
        feed_urls = [url.strip() for url in RSS_FEED_URLS.split(",") if url.strip()]
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
        logger.info("\n📝 Processing & Publishing Posts")
        logger.info("-" * 80)
        
        published_count = 0
        skipped_count = 0
        
        for idx, entry in enumerate(to_process, 1):
            entry_url = entry["url"]
            entry_title = entry["title"]
            
            logger.info(f"\n[{idx}/{len(to_process)}] Processing: {entry_title[:50]}...")
            
            # Check if already posted
            if self.posted_urls_manager.is_posted(entry_url):
                logger.info(f"   ⏭️  SKIPPED (already posted)")
                skipped_count += 1
                continue
            
            # Scrape content
            logger.info(f"   🕷️  Scraping content...")
            scraped_data = self.scraper.scrape_post(entry_url)
            
            if not scraped_data:
                logger.warning(f"   ⚠️ Failed to scrape, using RSS title only")
                html_content = f"<p>📌 Visit <a href='{entry_url}'>original post</a> for more details</p>"
            else:
                # Build rich HTML
                html_content = HTMLContentBuilder.build_post_html(scraped_data)
            
            # Publish
            logger.info(f"   📤 Publishing to Blogger...")
            labels = [AUTO_POST_LABEL, "Automated", "Curated"]
            
            post_id = self.blogger_service.publish_post(
                BLOG_ID,
                entry_title,
                html_content,
                labels=labels
            )
            
            if post_id:
                self.posted_urls_manager.add(entry_url)
                published_count += 1
                
                # Rate limiting: हर publish के बाद wait करें
                if idx < len(to_process):
                    logger.info(f"   ⏳ Waiting 2 seconds before next post...")
                    time.sleep(2)
            else:
                logger.warning(f"   ⚠️ Failed to publish")
        
        # Step 6: Save state
        self.posted_urls_manager.save()
        
        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("📊 EXECUTION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"📤 Published: {published_count} posts")
        logger.info(f"⏭️  Skipped: {skipped_count} posts")
        logger.info(f"📊 Total Posted (All-Time): {len(self.posted_urls_manager.urls)}")
        logger.info("=" * 80)
        logger.info("✅ Automation completed successfully!")
        logger.info("=" * 80)
        
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
