#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS to Blogger Automation Script - Advanced Version
- आज की सभी नई पोस्ट्स को तुरंत upload करेगा
- हर घंटे नई पोस्ट्स को automatically post करेगा
- हर post में "RSS Auto Post" label add करेगा
- Detailed logging provide करेगा
"""

import os
import json
import sys
import logging
from typing import List, Set, Optional
from datetime import datetime
import feedparser
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ==========================
# Logging Setup
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("automation.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# ==========================
# Config
# ==========================
BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "").strip()
CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
RSS_FEED_URLS = os.environ.get("RSS_FEED_URLS", "").strip()

POSTED_FILE = "posted_urls.json"
AUTO_POST_LABEL = "RSS Auto Post"

# ==========================
# Load / Save Posted URLs
# ==========================
def load_posted_urls() -> Set[str]:
    if not os.path.exists(POSTED_FILE):
        return set()

    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set()
    except Exception:
        return set()

def save_posted_urls(urls: Set[str]):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)

# ==========================
# Blogger Authentication
# ==========================
def get_blogger_service():
    if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise ValueError(
            "Missing env vars: BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, "
            "BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN"
        )

    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/blogger"]
    )

    creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)

# ==========================
# RSS Content Extraction
# ==========================
def extract_entry_content(entry) -> str:
    content = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
    if hasattr(content, "value"):
        content = content.value

    if not content or not str(content).strip():
        content = "<p>Content not available.</p>"
    else:
        content = str(content).strip()

    if not content.startswith("<"):
        content = f"<p>{content}</p>"

    source_link = getattr(entry, "link", "")
    if source_link:
        content += f"<br><br><p><em>Source: <a href='{source_link}'>{source_link}</a></em></p>"

    return content

# ==========================
# Today detection
# ==========================
def is_today_post(published_date: str) -> bool:
    if not published_date:
        return True

    try:
        from email.utils import parsedate_to_datetime
        published = parsedate_to_datetime(published_date)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return published.date() == today.date()
    except Exception:
        return True

# ==========================
# Publish to Blogger
# ==========================
def publish_post(service, entry, labels: List[str]):
    title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
    link = getattr(entry, "link", None)
    if not link:
        return False

    body = {
        "kind": "blogger#post",
        "title": title,
        "content": extract_entry_content(entry),
        "labels": labels
    }

    try:
        service.posts().insert(blogId=BLOG_ID, body=body).execute()
        logger.info(f"✅ Posted: {title}")
        logger.info(f"🏷️ Labels: {', '.join(labels)}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to post: {title} | Error: {e}")
        return False

# ==========================
# Main
# ==========================
def main():
    logger.info("=" * 80)
    logger.info("🚀 RSS to Blogger Automation - Advanced Version")
    logger.info("=" * 80)
    logger.info(f"⏰ Execution Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not BLOG_ID or not RSS_FEED_URLS:
        logger.error("BLOGGER_BLOG_ID या RSS_FEED_URLS set नहीं है")
        return

    feed_urls = [url.strip() for url in RSS_FEED_URLS.split(",") if url.strip()]
    if not feed_urls:
        logger.error("RSS_FEED_URLS empty है")
        return

    posted_urls = load_posted_urls()
    service = get_blogger_service()

    all_entries = []
    for feed_url in feed_urls:
        logger.info(f"📡 Reading feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            entries = getattr(feed, "entries", None) or []

            for entry in reversed(entries[:30]):
                post_link = getattr(entry, "link", None)
                if not post_link:
                    continue

                all_entries.append({
                    "title": getattr(entry, "title", "Untitled Post"),
                    "link": post_link,
                    "published": getattr(entry, "published", ""),
                    "content": entry
                })
        except Exception as e:
            logger.error(f"❌ Feed error for {feed_url}: {e}")

    if not all_entries:
        logger.warning("⚠️ No entries found in RSS feeds")
        return

    # Today's posts should be processed first
    todays_posts = [e for e in all_entries if is_today_post(e.get("published", ""))]
    other_posts = [e for e in all_entries if not is_today_post(e.get("published", ""))]
    to_process = todays_posts + other_posts

    labels = [AUTO_POST_LABEL, "Automated"]

    published_count = 0
    skipped_count = 0

    for entry in to_process:
        post_link = entry["link"]
        if post_link in posted_urls:
            logger.info(f"⏭️ Skipped duplicate: {entry['title']}")
            skipped_count += 1
            continue

        logger.info(f"📝 New post detected: {entry['title']}")
        if publish_post(service, entry["content"], labels):
            posted_urls.add(post_link)
            save_posted_urls(posted_urls)
            published_count += 1

    logger.info("=" * 80)
    logger.info(f"📊 Published: {published_count}")
    logger.info(f"📊 Skipped: {skipped_count}")
    logger.info("✅ Automation completed successfully!")
    logger.info("=" * 80)

if __name__ == "__main__":
    main()
