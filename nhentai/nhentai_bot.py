from dotenv import load_dotenv
import os, json, requests

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") 
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "nhentai", "data")

with open(os.path.join(DATA_DIR, "new.json"), "r", encoding="utf-8") as f:
    new_data = json.load(f)

with open(os.path.join(DATA_DIR, "old.json"), "r", encoding="utf-8") as f:
    past_data = json.load(f)

past_ids = {entry["id"] for entry in past_data}

new_galleries = [g for g in new_data if g["id"] not in past_ids]

for gallery in reversed(new_galleries):
    caption = f"🆔 ID: {gallery['id']}\n\n📛 Title: {gallery['title']}\n\n🏷️ Tags: {', '.join(gallery['tags'])}\n\n📄 Pages: {gallery['pages']}"
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": CHAT_ID,
            "caption": caption
        },
        files={
            "photo": requests.get(gallery["thumbnail_url"]).content
        }
    )

with open(os.path.join(DATA_DIR, "old.json"), "w", encoding="utf-8") as f:
    json.dump(new_data, f, ensure_ascii=False, indent=2)
