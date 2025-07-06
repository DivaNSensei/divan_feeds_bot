import os, json, requests, praw, html
from pathlib import Path

# === Config ===
SUBREDDIT = "gonewildaudio"
POST_LIMIT = 50
DATA_DIR = Path("data")
NEW_FILE = DATA_DIR / "reddit_new.json"
OLD_FILE = DATA_DIR / "reddit_old.json"

# === Load Secrets ===
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# === Init Reddit ===
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT
)

# === Helper Functions ===

def fetch_posts():
    posts = []
    for post in reddit.subreddit(SUBREDDIT).top(time_filter="week", limit=POST_LIMIT):
        if "f4" in post.title.lower():
            posts.append({
                "id": post.id,
                "title": post.title,
                "author": post.author.name if post.author else "[deleted]",
                "score": post.score,
                "permalink": f"https://reddit.com{post.permalink}"
            })
    return posts


def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []

def save_json(path, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def send_telegram(post):
    text = (
        f"<b>{html.escape(post['title'])}</b>\n"
        f"👤 by <code>{html.escape(post['author'])}</code>\n"
        f"👍 {post['score']} upvotes\n"
        f'<code>{html.escape(f"https://reddit.com{post['permalink']}")}</code>'
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    r = requests.post(url, json=payload)

def main():
    # Step 1: Fetch current top posts
    new_posts = fetch_posts()
    save_json(NEW_FILE, new_posts)

    # Step 2: Load old posts and find new ones
    old_posts = load_json(OLD_FILE)
    old_ids = {p['id'] for p in old_posts}
    fresh_posts = [p for p in new_posts if p['id'] not in old_ids]

    # Step 3: Send and save
    for post in fresh_posts:
        send_telegram(post)

    save_json(OLD_FILE, new_posts)

if __name__ == "__main__":
    main()
