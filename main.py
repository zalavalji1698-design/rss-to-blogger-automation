import os
import json
from typing import List, Dict, Any
import feedparser
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ----------------------------
# Config from environment
# ----------------------------
BLOG_ID = os.environ.get("BLOGGER_BLOG_ID")
RSS_FEED_URLS = os.environ.get("RSS_FEED_URLS", "").split(",")
CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN")

POSTED_FILE = "posted_urls.json"

# ----------------------------
# Load previously posted URLs
# ----------------------------
def load_posted_urls() -> set:
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

def save_posted_urls(urls: set):
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)

# ----------------------------
# Get Blogger Service
# ----------------------------
def get_blogger_service():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, BLOG_ID]):
        raise ValueError(
            "Missing one or more env vars: "
            "BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN, BLOGGER_BLOG_ID"
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

# ----------------------------
# Extract content safely
# ----------------------------
def get_entry_content(entry) -> str:
    content = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
    if hasattr(content, "value"):
        return content.value
    if isinstance(content, str) and content.strip():
        return content
    return "<p>No content available.</p>"

# ----------------------------
# Publish to Blogger
# ----------------------------
def publish_post(service, entry):
    title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
    link = getattr(entry, "link", None)
    content = get_entry_content(entry)

    if not link:
        return False

    body = {
        "kind": "blogger#post",
        "title": title,
        "content": content
    }

    try:
        service.posts().insert(blogId=BLOG_ID, body=body).execute()
        print(f"✅ Posted: {title}")
        return True
    except Exception as e:
        print(f"❌ Failed to post: {title} | Error: {e}")
        return False

# ----------------------------
# Main logic
# ----------------------------
def main():
    # Clean feed URLs
    feed_urls = [url.strip() for url in RSS_FEED_URLS if url.strip()]
    if not feed_urls:
        print("No RSS feeds configured. Please set RSS_FEED_URLS secret.")
        return

    posted_urls = load_posted_urls()
    service = get_blogger_service()

    for feed_url in feed_urls:
        print(f"\nChecking RSS feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            entries = getattr(feed, "entries", None) or []

            for entry in reversed(entries[:10]):
                post_link = getattr(entry, "link", None)
                if not post_link:
                    continue

                if post_link in posted_urls:
                    print(f"Already posted: {post_link}")
                    continue

                title = getattr(entry, "title", "Untitled Post") or "Untitled Post"
                print(f"New post found: {title}")

                posted = publish_post(service, entry)
                if posted:
                    posted_urls.add(post_link)
                    save_posted_urls(posted_urls)

        except Exception as e:
            print(f"Error while reading feed: {feed_url} | {e}")

    print("\nAutomation completed.")

if __name__ == "__main__":
    main()
