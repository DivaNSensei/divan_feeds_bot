import os, json, html, time, random, cloudscraper
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "hocean", "data")
OLD_PATH = os.path.join(DATA_DIR, "hocean_old.json")

URL = "https://hentaiocean.com/view/recent-releases"
HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_CAPTION_LENGTH = 1024

scraper = cloudscraper.create_scraper()

# -------------------- Fetch links --------------------
def fetch_recent_links():
    response = scraper.get(URL, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    section = soup.select_one("section.section div.container div.fixed-grid div.grid")
    if not section:
        return []

    links = [a['href'] for a in section.find_all("a", href=True)]
    return list(set(links))


def load_old_links():
    if not os.path.exists(OLD_PATH):
        return set()
    with open(OLD_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_links(links):
    with open(OLD_PATH, "w", encoding="utf-8") as f:
        json.dump(list(links), f, ensure_ascii=False, indent=2)


def get_fresh_links():
    current_links = fetch_recent_links()
    old_links = load_old_links()
    fresh_links = [link for link in current_links if link not in old_links]
    save_links(current_links)
    return fresh_links

# -------------------- Parse detail page --------------------
def parse_detail_page(url):
    try:
        response = scraper.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # Title
        title_tag = soup.select_one("section.section div.container h1.title")
        title = title_tag.text.strip() if title_tag else "N/A"

        # Thumbnail
        thumb_tag = soup.select_one("section.section div.container div.columns div.column img")
        thumbnail = f"https://hentaiocean.com{thumb_tag['src']}" if thumb_tag else ""

        # Info div
        info_div = soup.select_one("section.section div.container div.columns div.column.is-9")
        release_date = upload_date = synopsis = "N/A"
        if info_div:
            p_tags = info_div.find_all("p")
            for p in p_tags:
                text = p.get_text(strip=True)
                if text.startswith("Release date:"):
                    release_date = text.replace("Release date:", "").strip()
                elif text.startswith("Upload date:"):
                    upload_date = text.replace("Upload date:", "").strip()

            hr_tag = info_div.find("hr")
            if hr_tag:
                synopsis_parts = []
                for elem in hr_tag.next_siblings:
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
            "thumbnail": thumbnail
        }

    except Exception as e:
        print(f"Failed to parse {url}: {e}")
        return None

# -------------------- Send to Telegram --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram_messages(new_h, bot_token, chat_id):
    for h in reversed(new_h):
        if not h or not h.get("thumbnail"):
            continue

        # Build initial static caption
        static_caption = (
            f"<b>*** {html.escape(h['title'])} ***</b>\n"
            f"Release Date: {h['release_date']}\n"
            f"Upload Date: {h['upload_date']}\n"
            f"<code>{html.escape(h['url'])}</code>\n"
            f"Synopsis: <i>"
        )

        # Calculate remaining chars for synopsis
        remaining_len = MAX_CAPTION_LENGTH - len(static_caption) - len("</i>")

        # Dynamically truncate synopsis, title, and URL if needed
        synopsis = h["synopsis"]
        title = h["title"]
        url = h["url"]

        # Truncate synopsis first
        if len(synopsis) > remaining_len:
            synopsis = synopsis[:remaining_len - 3] + "..."

        # Build final caption
        caption = (
            f"<b>*** {html.escape(title)} ***</b>\n"
            f"Release Date: {h['release_date']}\n"
            f"Upload Date: {h['upload_date']}\n"
            f"<code>{html.escape(url)}</code>\n"
            f"Synopsis: <i>{html.escape(synopsis)}</i>"
        )

        try:
            resp = scraper.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                data={
                    "chat_id": chat_id,
                    "photo": h["thumbnail"],
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                timeout=10
            )
            if resp.status_code != 200:
                print(f"Telegram send failed for {title}: {resp.text}")
        except Exception as e:
            print(f"Error sending Telegram message for {title}: {e}")

        # Random delay to prevent hitting rate limits
        time.sleep(random.uniform(1, 2))


# -------------------- Main --------------------
def main():
    fresh_links = get_fresh_links()
    if not fresh_links:
        print("No new releases found.")
        return

    new_h = []
    for url in fresh_links:
        parsed = parse_detail_page(url)
        if parsed:
            new_h.append(parsed)
        time.sleep(random.uniform(0.5, 1.5))  # delay between detail page requests

    send_telegram_messages(new_h, BOT_TOKEN, CHAT_ID)


if __name__ == "__main__":
    main()
