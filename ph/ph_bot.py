from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import os, json, requests, html, time

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "ph", "data")
OLD_PATH = os.path.join(DATA_DIR, "ph_old.json")

url = "https://www.pornhub.com/video?p=homemade&o=mv"

def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, timeout=60000)
        try:
            page.wait_for_selector("button.js-closeAgeModal", timeout=10000)
            page.click("button.js-closeAgeModal")
        except Exception:
            print("Age confirmation button not found or already dismissed.")
        time.sleep(3)
        html_content = page.content()
        browser.close()
        return html_content

def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    video_ul = soup.find("ul", class_="nf-videos videos search-video-thumbs")

    videos = video_ul.find_all("li", class_="pcVideoListItem")
    video_data = []

    for video in videos:
        a_tag = video.find("a", href=True)
        if not a_tag:
            continue

        href = a_tag['href']
        title = a_tag.get('data-title', '').strip()
        img_tag = a_tag.find("img")
        thumbnail = img_tag['src']
        duration_tag = a_tag.find("var", class_="duration")
        duration = duration_tag.text.strip()

        uploader_tag = video.find("div", class_="usernameWrap")
        uploader_a = uploader_tag.find("a")
        uploader = uploader_a.text.strip()

        views_tag = video.find("span", class_="views")
        views_var = views_tag.find("var")
        views = views_var.text.strip()

        v_id = href.split("viewkey=")[-1]
        video_data.append({
            "id": v_id,
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "uploader": uploader,
            "views": views,
        })
    return video_data

def send_telegram_messages(new_videos, bot_token, chat_id):
    for video in reversed(new_videos):
        text = (
            f"<b>{html.escape(video['title'])}</b>\n"
            f"Uploader: <code>{html.escape(video['uploader'])}</code>\n"
            f"Views: {video['views']}\n"
            f"Duration: {video['duration']}\n"
            f"<code>https://www.pornhub.com/view_video.php?viewkey={html.escape(video['id'])}</code>"
        )
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data={
                "chat_id": chat_id,
                "photo": video["thumbnail"],
                "caption": text,
                "parse_mode": "HTML",
            }
        )

def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")

    html_ = scrape()
    video_data = parse(html_)

    if not os.path.exists(OLD_PATH):
        old_data = []
    else:
        with open(OLD_PATH, "r", encoding="utf-8") as f:
            old_data = json.load(f)

    past_ids = {entry["id"] for entry in old_data}
    new_videos = [v for v in video_data if v["id"] not in past_ids]

    if new_videos:
        send_telegram_messages(new_videos, BOT_TOKEN, CHAT_ID)
        with open(OLD_PATH, "w", encoding="utf-8") as f:
            json.dump(video_data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
