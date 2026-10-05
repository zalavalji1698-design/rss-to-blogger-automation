import os
import json
import sys
import logging
from typing import List, Set, Optional
import feedparser
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ==========================
# Logger Setup
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==========================
# Environment Variables
# ==========================
BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "").strip()
CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
RSS_FEED_URLS = os.environ.get("RSS_FEED_URLS", "").strip()

POSTED_FILE = "posted_urls.json"

# ==========================
# Load Posted URLs
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

# ==========================
# Save Posted URLs
# ==========================
def save_posted_urls(urls: Set[str]):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)

# ==========================
# Get Blogger Credentials
# ==========================
def get_blogger_service():
    if not all([BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise ValueError(
            "Missing required env vars: "
            "BLOGGER_BLOG_ID, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN"
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
    service = build("blogger", "v3", credentials=creds)
    return service

# ==========================
# Get Entry Content
# ==========================
def get_entry_content(entry) -> str:
    content = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
    if hasattr(content, "value"):
        return content.value
    if isinstance(content, str) and content.strip():
        return content
    return "<p>No content available.</p>"

# ==========================
# Publish Post
# ==========================
def publish_post(service, entry):
    title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
    link = getattr(entry, "link", None)
    if not link:
        return False

    body = {
        "kind": "blogger#post",
        "title": title,
        "content": get_entry_content(entry)
    }

    try:
        service.posts().insert(blogId=BLOG_ID, body=body).execute()
        print(f"✅ Posted: {title}")
        return True
    except Exception as e:
        print(f"❌ Failed to post: {title} | {e}")
        return False

# ==========================
# Main Script
# ==========================
def main():
    if not BLOG_ID or not RSS_FEED_URLS:
        logger.error("BLOGGER_BLOG_ID और RSS_FEED_URLS environment variable set नहीं है।")
        return

    feed_urls = [url.strip() for url in RSS_FEED_URLS.split(",") if url.strip()]
    if not feed_urls:
        logger.error("RSS_FEED_URLS खाली है।")
        return

    posted_urls = load_posted_urls()
    service = get_blogger_service()

    for feed_url in feed_urls:
        print(f"\nChecking RSS feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            entries = getattr(feed, "entries", None) or []

            if not entries:
                print("No entries found in this feed.")
                continue

            for entry in reversed(entries[:10]):
                post_link = getattr(entry, "link", None)
                if not post_link:
                    continue

                if post_link in posted_urls:
                    print(f"Already posted: {post_link}")
                    continue

                print(f"New post found: {getattr(entry, 'title', 'Untitled Post')}")
                if publish_post(service, entry):
                    posted_urls.add(post_link)
                    save_posted_urls(posted_urls)

        except Exception as e:
            print(f"RSS parse error for {feed_url}: {e}")

    print("\nAutomation completed.")

if __name__ == "__main__":
    main()
