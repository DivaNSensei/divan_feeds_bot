from dotenv import load_dotenv
import os, json, requests

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") 

with open("nhentai/data/new.json", "r", encoding="utf-8") as f:
    new_data = json.load(f)

with open("nhentai/data/past.json", "r", encoding="utf-8") as f:
    past_data = json.load(f)

past_ids = {entry["id"] for entry in past_data}

new_galleries = [g for g in new_data if g["id"] not in past_ids]

for gallery in new_galleries:
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

with open("nhentai/data/past.json", "w", encoding="utf-8") as f:
    json.dump(new_data, f, ensure_ascii=False, indent=2)
