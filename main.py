#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS to Blogger Automation - Gujarati Version
આપેલા HTML ટેમ્પલેટ સાથે નવી પોસ્ટો ઓટોમેટિક પોસ્ટ કરે
- RSS feeds વાંચે
- ફક્ત નવી પોસ્ટો પ્રોસેસ કરે (Duplicate પોસ્ટો રોકે)
- Title, Description, Image, Video URL extract કરે
- Custom HTML ટેમ્પલેટ અમલ કરે
- Blogger માં પોસ્ટ કરે
"""

import os
import json
import sys
import logging
import requests
import time
import re
import base64
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
# Environment Configuration
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
    """પહેલાંથી post કરેલ URLs ને manage કરે"""

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
                    return set(str(x).strip() for x in data if x)
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
        return url.strip() in self.urls

    def add(self, url: str):
        if url.strip():
            self.urls.add(url.strip())

# =====================================
# Web Content Scraper
# =====================================
class ContentScraper:
    """RSS post URLs માંથી content scrape કરે અને media extract કરે"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Retry strategy સાથે session બનાવે"""
        session = requests.Session()

        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            backoff_factor=1
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
        })

        return session

    def scrape_post(self, url: str) -> Optional[Dict]:
        """આપેલ URL માંથી post ડેટા scrape કરે"""
        try:
            logger.info(f"🕷️  Scraping: {url}")

            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = 'utf-8'

            soup = BeautifulSoup(response.content, 'html.parser')

            title = self._extract_title(soup, url)
            description = self._extract_description(soup)
            image = self._extract_image(soup, url)
            video = self._extract_video(soup, url)

            logger.info(f"   ✅ Extracted: Title, Description, Image: {bool(image)}, Video: {bool(video)}")

            return {
                'title': title,
                'description': description,
                'image': image,
                'video': video,
                'original_url': url
            }

        except Exception as e:
            logger.error(f"   ❌ Scrape error: {e}")
            return None

    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        """Page માંથી title નીકાળે"""
        # h1 tag
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
            if title:
                return title

        # og:title
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        # twitter:title
        twitter_title = soup.find('meta', attrs={'name': 'twitter:title'})
        if twitter_title and twitter_title.get('content'):
            return twitter_title['content'].strip()

        # page title
        title_tag = soup.find('title')
        if title_tag:
            return title_tag.get_text(strip=True)

        return urlparse(url).netloc

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Page માંથી description નીકાળે"""
        # meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            text = meta_desc['content'].strip()
            if text and len(text) > 20:
                return text[:500]

        # og:description
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        if og_desc and og_desc.get('content'):
            text = og_desc['content'].strip()
            if text and len(text) > 20:
                return text[:500]

        # પહેલું paragraph
        for tag in soup.find_all(['p', 'div', 'summary'], limit=5):
            text = tag.get_text(strip=True)
            if text and len(text) > 50:
                return text[:500]

        return "વધુ માહિતી માટે મૂળ લેખ વાંચો."

    def _extract_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Featured image નીકાળે"""
        # og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content'].strip()
            if self._is_valid_image(img_url):
                return img_url

        # twitter:image
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            img_url = twitter_image['content'].strip()
            if self._is_valid_image(img_url):
                return img_url

        # img tags
        for img in soup.find_all('img', limit=10):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                full_url = urljoin(base_url, src.strip())
                if self._is_valid_image(full_url):
                    return full_url

        return None

    def _extract_video(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Video URL નીકાળે (MP4, iframe, YouTube, etc.)"""
        video_url = None

        # iframe video
        for iframe in soup.find_all('iframe', limit=5):
            src = iframe.get('src', '').strip()
            if src:
                lower_src = src.lower()
                # YouTube
                if 'youtube' in lower_src or 'youtu.be' in lower_src:
                    return src
                # Vimeo
                if 'vimeo' in lower_src:
                    return src
                # અન્ય video platforms
                if any(p in lower_src for p in ['dailymotion', 'rumble', 'odysee', 'desikahani']):
                    return src

        # direct video tag
        for video_tag in soup.find_all('video', limit=5):
            src = video_tag.get('src')
            if src:
                full_url = urljoin(base_url, src.strip())
                return full_url

            # video source tag
            for source in video_tag.find_all('source'):
                src = source.get('src')
                if src:
                    full_url = urljoin(base_url, src.strip())
                    if full_url.lower().endswith(('.mp4', '.webm', '.m4v')):
                        return full_url

        # meta video tags
        og_video = soup.find('meta', property='og:video')
        if og_video and og_video.get('content'):
            video_url = og_video['content'].strip()
            if video_url and any(x in video_url.lower() for x in ['.mp4', '.m3u8', 'youtube', 'vimeo']):
                return video_url

        # data-video attributes
        for elem in soup.find_all(['div', 'a'], limit=10):
            for attr in ['data-video-url', 'data-src', 'data-video']:
                video_url = elem.get(attr)
                if video_url:
                    full_url = urljoin(base_url, video_url.strip())
                    if any(x in full_url.lower() for x in ['.mp4', '.m3u8', 'youtube', 'vimeo']):
                        return full_url

        return None

    def _is_valid_image(self, url: str) -> bool:
        """Check કે image URL valid છે કે નહીં"""
        if not url:
            return False

        url_lower = url.lower()

        # Invalid patterns
        if any(x in url_lower for x in ['ads', 'tracking', 'pixel', 'spacer', '1x1', 'favicon']):
            return False

        # Valid extensions
        if any(url_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']):
            return True

        # CDNs
        if any(cdn in url_lower for cdn in ['cloudinary', 'imgix', 'imageserve', 'pbs.twimg', 'imgur']):
            return True

        return False

# =====================================
# HTML Template Renderer with Video Player
# =====================================
class BlogHTMLRenderer:
    """Scraped data ને custom HTML template મા render કરે"""

    # આપેલું HTML template - VIDEO PLAYER + ADS + CUSTOM DESIGN
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

  <div class="ump-ad ump-ad-468">
    <script>
      atOptions = {{
        'key' : 'c02c0defb48d592cc7e25fbb0115e63a',
        'format' : 'iframe',
        'height' : 60,
        'width' : 468,
        'params' : {{}}
      }};
    </script>
    <script src="https://sponsorinserttimeout.com/c02c0defb48d592cc7e25fbb0115e63a/invoke.js"></script>
  </div>

  <div class="ump-description">{POST_DESCRIPTION}</div>

  {IMAGE_BLOCK}

  <div class="ump-ad ump-native-ad">
    <script async="async" data-cfasync="false" src="https://sponsorinserttimeout.com/98ba824931c676f939dd9b860ee5df26/invoke.js"></script>
    <div id="container-98ba824931c676f939dd9b860ee5df26"></div>
  </div>

  <div class="ump-scroll-reminder">
    👇 <strong>થોડું નીચે સ્ક્રોલ કરો — Video Player અહીં છે</strong>
  </div>

  <div id="umpVideoPlayer" class="ump-player-box">
    <div id="umpLoading" class="ump-loading">Loading Video Player...</div>
    <div id="umpPlayer"></div>
  </div>

  <div id="umpDownloadBox" class="ump-download-box" style="display:none;">
    <a id="umpDownload" class="ump-download-button" href="#" target="_blank" rel="noopener">⬇️ Download Video</a>
  </div>

  <div class="ump-smart-link">
    <a href="https://sponsorinserttimeout.com/ev5dpcs4vh?key=cb3a4c8dd890dc920a863efe7564cdac" target="_blank" rel="nofollow noopener">
      ▶ Continue / Watch
    </a>
  </div>

  <div class="ump-ad ump-ad-728">
    <script>
      atOptions = {{
        'key' : 'a0a802a872bc486ec685fb758c8eef4b',
        'format' : 'iframe',
        'height' : 90,
        'width' : 728,
        'params' : {{}}
      }};
    </script>
    <script src="https://sponsorinserttimeout.com/a0a802a872bc486ec685fb758c8eef4b/invoke.js"></script>
  </div>

  <div class="ump-final-info">
    <strong>🎬 Video Player</strong>
    <p>ઉપરના Player માંથી વિડિયો ચલાવી શકો છો.</p>
  </div>

</div>

{CSS_STYLES}

<script>
{PLAYER_SCRIPT}
</script>

<script src="https://sponsorinserttimeout.com/ec/86/73/ec867370afff042880ed8bcfe2b6caaa.js"></script>
"""

    CSS_STYLES = """
<style>
#ump2026{
  width:100%;
  max-width:900px;
  margin:15px auto;
  padding:10px;
  box-sizing:border-box;
  font-family:Arial, Helvetica, sans-serif;
}

#ump2026 .ump-title{
  margin:0 0 18px;
  padding:0;
  font-size:28px;
  line-height:1.4;
  font-weight:700;
  text-align:center;
}

#ump2026 .ump-video-notice{
  width:100%;
  box-sizing:border-box;
  display:flex;
  align-items:center;
  gap:12px;
  margin:15px 0 20px;
  padding:14px;
  background:linear-gradient(135deg, #fff7d6, #fff1a8);
  border:2px solid #f0c400;
  border-radius:12px;
  box-shadow:0 4px 12px rgba(0,0,0,.10);
}

#ump2026 .ump-notice-icon{
  font-size:28px;
  animation:umpArrowMove 1s infinite;
}

#ump2026 .ump-notice-text{
  flex:1;
  display:flex;
  flex-direction:column;
  gap:3px;
}

#ump2026 .ump-notice-text strong{
  font-size:16px;
  color:#222;
}

#ump2026 .ump-notice-text span{
  font-size:13px;
  color:#555;
}

#ump2026 .ump-watch-button{
  display:inline-block;
  white-space:nowrap;
  padding:10px 15px;
  background:#e60000;
  color:#fff !important;
  text-decoration:none !important;
  border-radius:8px;
  font-size:14px;
  font-weight:bold;
}

#ump2026 .ump-watch-button:hover{
  opacity:.85;
}

@keyframes umpArrowMove{
  0%,100%{ transform:translateY(0); }
  50%{ transform:translateY(7px); }
}

#ump2026 .ump-description{
  margin:15px 0 20px;
  font-size:16px;
  line-height:1.75;
  color:#222;
}

#ump2026 .ump-image{
  width:100%;
  margin:15px 0 20px;
  text-align:center;
}

#ump2026 .ump-image img{
  width:100%;
  max-width:900px;
  height:auto;
  display:block;
  margin:auto;
  border-radius:12px;
}

#ump2026 .ump-ad{
  width:100%;
  text-align:center;
  margin:20px auto;
  overflow:hidden;
}

#ump2026 .ump-ad-468{ min-height:60px; }
#ump2026 .ump-ad-728{ min-height:90px; }
#ump2026 .ump-native-ad{ min-height:100px; }

#ump2026 .ump-scroll-reminder{
  width:100%;
  box-sizing:border-box;
  text-align:center;
  margin:18px 0;
  padding:12px;
  background:#eef7ff;
  border:1px dashed #1683ff;
  border-radius:8px;
  color:#1261a0;
  font-size:14px;
}

#ump2026 .ump-player-box{
  width:100%;
  min-height:220px;
  background:#000;
  border-radius:12px;
  overflow:hidden;
  margin:20px 0;
  position:relative;
}

#ump2026 #umpPlayer{
  width:100%;
}

#ump2026 #umpPlayer video{
  width:100%;
  height:auto;
  min-height:200px;
  display:block;
  background:#000;
}

#ump2026 #umpPlayer iframe{
  width:100%;
  height:500px;
  display:block;
  border:0;
  background:#000;
}

#ump2026 .ump-loading{
  color:#fff;
  text-align:center;
  padding:30px 10px;
  font-size:15px;
}

#ump2026 .ump-download-box{
  text-align:center;
  margin:20px 0;
}

#ump2026 .ump-download-button{
  display:inline-block;
  padding:13px 28px;
  background:linear-gradient(135deg, #1769ff, #0044cc);
  color:#fff !important;
  text-decoration:none !important;
  border-radius:8px;
  font-size:16px;
  font-weight:bold;
  box-shadow:0 4px 10px rgba(0,0,0,.18);
}

#ump2026 .ump-download-button:hover{
  transform:translateY(-1px);
}

#ump2026 .ump-smart-link{
  text-align:center;
  margin:18px 0;
}

#ump2026 .ump-smart-link a{
  display:inline-block;
  padding:12px 25px;
  background:#222;
  color:#fff !important;
  text-decoration:none !important;
  border-radius:8px;
  font-weight:bold;
}

#ump2026 .ump-final-info{
  margin:20px 0;
  padding:15px;
  background:#f5f5f5;
  border-radius:10px;
  text-align:center;
}

#ump2026 .ump-final-info p{
  margin:7px 0 0;
  font-size:14px;
}

@media(max-width:600px){
  #ump2026{ padding:8px; }
  #ump2026 .ump-title{ font-size:22px; }
  #ump2026 .ump-description{ font-size:15px; }
  #ump2026 #umpPlayer iframe{ height:260px; }
  #ump2026 #umpPlayer video{ min-height:200px; }
}

html{ scroll-behavior:smooth; }
</style>
"""

    PLAYER_SCRIPT_TEMPLATE = """
(function(){{
  var MEDIA_URL = "{MEDIA_URL}";
  var player = document.getElementById("umpPlayer");
  var loading = document.getElementById("umpLoading");
  var downloadBox = document.getElementById("umpDownloadBox");
  var download = document.getElementById("umpDownload");

  if(!MEDIA_URL || MEDIA_URL === "MEDIA_URL"){{
    loading.innerHTML = "Video URL is not configured.";
    return;
  }}

  var lowerURL = MEDIA_URL.toLowerCase();
  var videoExtensions = [".mp4", ".webm", ".ogg", ".ogv", ".m4v", ".mov"];
  var isDirectVideo = false;

  for(var i = 0; i < videoExtensions.length; i++){{
    if(lowerURL.indexOf(videoExtensions[i]) !== -1){{
      isDirectVideo = true;
      break;
    }}
  }}

  var isM3U8 = lowerURL.indexOf(".m3u8") !== -1;

  if(isDirectVideo){{
    var video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("controlsList", "nodownload");
    
    var source = document.createElement("source");
    source.src = MEDIA_URL;
    video.appendChild(source);
    player.appendChild(video);
    loading.style.display = "none";

    download.href = MEDIA_URL;
    download.download = "";
    downloadBox.style.display = "block";
  }}
  else if(isM3U8){{
    var video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.style.width = "100%";
    video.style.background = "#000";
    player.appendChild(video);

    var hlsScript = document.createElement("script");
    hlsScript.src = "https://cdn.jsdelivr.net/npm/hls.js@latest";
    hlsScript.onload = function(){{
      if(window.Hls && Hls.isSupported()){{
        var hls = new Hls();
        hls.loadSource(MEDIA_URL);
        hls.attachMedia(video);
      }}
      else if(video.canPlayType("application/vnd.apple.mpegurl")){{
        video.src = MEDIA_URL;
      }}
      loading.style.display = "none";
    }};
    document.head.appendChild(hlsScript);
  }}
  else{{
    var iframe = document.createElement("iframe");
    iframe.src = MEDIA_URL;
    iframe.setAttribute("allowfullscreen", "true");
    iframe.setAttribute("allow", "autoplay; fullscreen; picture-in-picture");
    iframe.setAttribute("frameborder", "0");
    iframe.loading = "lazy";
    player.appendChild(iframe);
    loading.style.display = "none";
  }}
}})();
"""

    @staticmethod
    def render(post_title: str, description: str, image_url: Optional[str], 
               video_url: Optional[str], original_url: str) -> str:
        """Render કરેલું HTML template બનાવે"""
        
        # Image block
        image_block = ""
        if image_url:
            image_block = f"""
  <div class="ump-image">
    <img src="{image_url}" alt="{post_title}" loading="lazy">
  </div>
"""

        # Video URL (embed આવશ્યક છે)
        video_url_safe = video_url or "about:blank"

        # JavaScript player script
        player_script = BlogHTMLRenderer.PLAYER_SCRIPT_TEMPLATE.format(
            MEDIA_URL=video_url_safe
        )

        # Final HTML
        html = BlogHTMLRenderer.TEMPLATE.format(
            POST_TITLE=post_title,
            POST_DESCRIPTION=description,
            IMAGE_BLOCK=image_block,
            CSS_STYLES=BlogHTMLRenderer.CSS_STYLES,
            PLAYER_SCRIPT=player_script
        )

        return html

# =====================================
# Blogger API Service
# =====================================
class BloggerService:
    """Google Blogger API સાથે interact કરે"""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.service = None

    def authenticate(self) -> bool:
        try:
            logger.info("🔐 Google Blogger API સાથે authenticate કરી રહ્યા છીએ...")

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
            logger.info("✅ Authentication સફળ રહ્યું")
            return True

        except Exception as e:
            logger.error(f"❌ Authentication નિષ્ફળ: {e}")
            return False

    def publish_post(self, blog_id: str, title: str, content: str, 
                    labels: Optional[List[str]] = None) -> Optional[str]:
        """Blogger માં post publish કરે"""
        if not self.service:
            logger.error("❌ Blogger service authenticate નથી")
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

                logger.info(f"📤 Post publish કરી રહ્યા છીએ (attempt {retry_count + 1}/{max_retries})...")
                request = self.service.posts().insert(blogId=blog_id, body=body)
                response = request.execute()

                post_id = response.get("id")
                post_url = response.get("url")

                logger.info(f"✅ Published: {title[:50]}")
                logger.info(f"   🔗 URL: {post_url}")
                logger.info(f"   🏷️  Labels: {', '.join(labels) if labels else 'None'}")

                return post_id

            except Exception as e:
                error_str = str(e)

                if "429" in error_str or "quota" in error_str.lower() or "exhausted" in error_str.lower():
                    retry_count += 1

                    if retry_count < max_retries:
                        wait_time = base_wait_time ** retry_count
                        logger.warning(f"⏸️  RATE LIMITED (429 / Quota Exceeded)")
                        logger.warning(f"   ⏳ {wait_time}s પછી retry કરીશું...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"❌ Max retries exceeded")
                        return None

                logger.error(f"❌ Publish નિષ્ફળ: {e}")
                return None

        return None

# =====================================
# RSS Feed Parser
# =====================================
class RSSFeedParser:
    """RSS feeds parse કરે"""

    @staticmethod
    def parse_feeds(feed_urls: List[str], max_entries: int = 30) -> List[Dict]:
        """બધી feeds પાર્સ કરે"""
        all_entries = []

        for feed_url in feed_urls:
            if not feed_url.strip():
                continue

            try:
                logger.info(f"📡 RSS feed વાંચી રહ્યા છીએ: {feed_url}")

                feed = feedparser.parse(feed_url)
                entries = getattr(feed, "entries", []) or []

                if not entries:
                    logger.warning(f"   ⚠️ કોઈ entries મળ્યા નહીં")
                    continue

                logger.info(f"   📝 {len(entries)} entries મળ્યા")

                for entry in reversed(entries[:max_entries]):
                    entry_link = getattr(entry, "link", None)
                    if not entry_link:
                        continue

                    entry_title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
                    entry_published = getattr(entry, "published", "") or getattr(entry, "updated", "")

                    all_entries.append({
                        "url": str(entry_link).strip(),
                        "title": str(entry_title).strip(),
                        "published": str(entry_published).strip()
                    })

            except Exception as e:
                logger.error(f"   ❌ Feed parse error: {e}")
                continue

        logger.info(f"🎯 કુલ {len(all_entries)} entries એકત્રિત કરેલ")
        return all_entries

# =====================================
# Main Automation
# =====================================
class RSSBloggerAutomation:
    """Main automation orchestrator"""

    def __init__(self):
        self.posted_urls = PostedURLsManager()
        self.scraper = ContentScraper()
        self.blogger = BloggerService(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)

    def run(self) -> bool:
        logger.info("=" * 100)
        logger.info("🚀 RSS to Blogger Automation with Custom HTML Template")
        logger.info("=" * 100)
        logger.info(f"⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Validate config
        if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, RSS_FEED_URLS]):
            logger.error("❌ અપૂર્ણ environment variables")
            logger.error("   જરૂરી: BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, RSS_FEED_URLS")
            return False

        # Authenticate
        if not self.blogger.authenticate():
            return False

        # Parse feeds
        feed_urls = [u.strip() for u in RSS_FEED_URLS.split(",") if u.strip()]
        logger.info(f"📡 {len(feed_urls)} feed(s) વાંચી રહ્યા છીએ")
        all_entries = RSSFeedParser.parse_feeds(feed_urls, max_entries=40)

        if not all_entries:
            logger.warning("⚠️ કોઈ entries મળ્યા નહીં")
            return True

        # Filter only new entries
        new_entries = []
        for entry in all_entries:
            url = entry.get("url", "").strip()
            if url and not self.posted_urls.is_posted(url):
                new_entries.append(entry)

        logger.info(f"🆕 {len(new_entries)} નવી entries મળી")

        if not new_entries:
            logger.info("✅ કોઈ નવી entries નથી. પોસ્ટ કરવા માટે કશું નથી.")
            return True

        logger.info("\n📝 Processing & Publishing Posts")
        logger.info("-" * 100)

        published_count = 0
        failed_count = 0

        for idx, entry in enumerate(new_entries, 1):
            entry_url = entry.get("url", "").strip()
            entry_title = entry.get("title", "Untitled Post").strip()

            logger.info(f"\n[{idx}/{len(new_entries)}] Processing: {entry_title[:70]}")

            # Scrape content
            scraped_data = self.scraper.scrape_post(entry_url)

            if not scraped_data:
                logger.warning("   ⚠️ Scrape નિષ્ફળ - Fallback using RSS data")
                title = entry_title
                description = "વધુ માહિતી માટે મૂળ લેખ વાંચો."
                image = None
                video = None
            else:
                title = scraped_data.get("title") or entry_title
                description = scraped_data.get("description") or "વધુ માહિતી માટે મૂળ લેખ વાંચો."
                image = scraped_data.get("image")
                video = scraped_data.get("video")

            logger.info(f"      📊 Media: Image={bool(image)}, Video={bool(video)}")

            # Render HTML
            html_content = BlogHTMLRenderer.render(
                title,
                description,
                image,
                video,
                entry_url
            )

            # Publish
            logger.info("   📤 Blogger માં publish કરી રહ્યા છીએ...")
            labels = [AUTO_POST_LABEL, "Automated", "RSS Feed", "Custom Template"]

            post_id = self.blogger.publish_post(
                BLOG_ID,
                title,
                html_content,
                labels=labels
            )

            if post_id:
                self.posted_urls.add(entry_url)
                self.posted_urls.save()
                published_count += 1

                if idx < len(new_entries):
                    logger.info("   ⏳ આગલી post માટે 4 સેકંડ રાહ જોઈશું...")
                    time.sleep(4)
            else:
                failed_count += 1
                logger.warning("   ⚠️ Publish નિષ્ફળ")

        # Summary
        logger.info("\n" + "=" * 100)
        logger.info("📊 EXECUTION SUMMARY")
        logger.info("=" * 100)
        logger.info(f"✅ Published: {published_count} posts")
        logger.info(f"❌ Failed: {failed_count} posts")
        logger.info(f"📊 Total Posts (All-Time): {len(self.posted_urls.urls)}")
        logger.info("=" * 100)

        if published_count > 0 or failed_count == 0:
            logger.info("✅ Automation સફળ રહ્યું!")
        else:
            logger.warning("⚠️ Automation કેટલીક સમસ્યાઓ સાથે પૂર્ણ થયું!")

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
        logger.error(f"❌ અનપેક્ષિત error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
