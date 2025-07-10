import os, json, html
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "hocean", "data")
OLD_PATH = os.path.join(DATA_DIR, "hocean_old.json")

URL = "https://hentaiocean.com/view/recent-releases"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch_recent_links():
    response = requests.get(URL, headers=HEADERS)
    soup = BeautifulSoup(response.text, "html.parser")

    section = soup.select_one("section.section div.container div.fixed-grid div.grid")
    links = [a['href'] for a in section.find_all("a", href=True)]
    full_links = list(set(links))

    return full_links

def load_old_links():
    with open(OLD_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))

def save_links(links):
    with open(OLD_PATH, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)

def get_fresh_links():
    current_links = fetch_recent_links()
    old_links = load_old_links()
    fresh_links = [link for link in current_links if link not in old_links]


    save_links(current_links)
    return fresh_links


def parse_detail_page(url):
    response = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(response.text, "html.parser")

    title_tag = soup.select_one("section.section div.container h1.title")
    title = title_tag.text.strip()

    thumb_tag = soup.select_one("section.section div.container div.columns div.column img")
    thumbnail = thumb_tag["src"]

    info_div = soup.select_one("section.section div.container div.columns div.column.is-9")
    release_date = upload_date = synopsis = "N/A"

    if info_div:
        p_tags = info_div.find_all("p")
        for p in p_tags:
            if "Release date:" in p.text:
                release_date = p.text.replace("Release date:", "").strip()
            elif "Upload date:" in p.text:
                upload_date = p.text.replace("Upload date:", "").strip()

        hr_tag = info_div.find("hr")
        if hr_tag:
            synopsis_parts = []
            for elem in hr_tag.next_siblings:
                text = ''
                if hasattr(elem, 'get_text'):
                    text = elem.get_text(strip=True)
                else:
                    text = str(elem).strip()
                if text:
                    synopsis_parts.append(text)
            synopsis = "\n".join(synopsis_parts).strip()


    return {
        "url": url,
        "title": title,
        "release_date": release_date,
        "upload_date": upload_date,
        "synopsis": synopsis,
        "thumbnail": f"https://hentaiocean.com{thumbnail}"
    }

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") 

def send_telegram_messages(new_h, bot_token, chat_id):
    for h in reversed(new_h):
        text = (
            f"<b>{html.escape(h['title'])}</b>\n"
            f"Release Date: {h['release_date']}\n"
            f"Upload Date: {h['upload_date']}\n"
            f"Synopsis: <i>{h['synopsis']}</i>\n"
            f"<code>{html.escape(h['url'])}</code>"
        )
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendPhoto",
            data={
                "chat_id": chat_id,
                "photo": h["thumbnail"],
                "caption": text,
                "parse_mode": "HTML",
            }
        )

def main():
    new_links = get_fresh_links()
    if not new_links:
        return
    new_h = [parse_detail_page(url) for url in new_links]
    send_telegram_messages(new_h, BOT_TOKEN, CHAT_ID)

if __name__ == "__main__":
    main()
