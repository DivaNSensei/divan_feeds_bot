from dotenv import load_dotenv
import os, json, time, random, cloudscraper
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "nhentai", "data")
OLD_PATH = os.path.join(DATA_DIR, "old.json")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ----------------------------
# Step 1: Use Cloudscraper to fetch the homepage
# ----------------------------
scraper = cloudscraper.create_scraper()
home_url = "https://nhentai.net/"
resp = scraper.get(home_url)
if resp.status_code != 200:
    raise Exception(f"Failed to fetch homepage: {resp.status_code}")

soup = BeautifulSoup(resp.text, "html.parser")
parent_div = soup.find("div", class_="container index-container index-popular")
if not parent_div:
    raise Exception("Couldn't find the popular container with cloudscraper")

galleries = parent_div.find_all("div", class_="gallery")
gallery_ids = []
id_to_title = {}

for gallery in galleries:
    a_tag = gallery.find("a", href=True)
    if not a_tag:
        continue
    href = a_tag['href']
    caption_div = a_tag.find("div", class_="caption")
    if not caption_div:
        continue
    title = caption_div.text.strip()
    gid = href.split("/")[2]
    gallery_ids.append(gid)
    id_to_title[gid] = title

# ----------------------------
# Step 2: Selenium headless setup for individual gallery pages
# ----------------------------
chrome_options = Options()
chrome_options.add_argument("--headless=new")  # use the new headless mode
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=chrome_options)

results = []

for gid in gallery_ids:
    url = f"https://nhentai.net/g/{gid}/"
    driver.get(url)
    time.sleep(random.uniform(1, 2))  # give JS time to render

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, "html.parser")

    # extract tags
    tag_section = soup.find("section", id="tags")
    tag_containers = tag_section.find_all("div", class_=["tag-container", "field-name"]) if tag_section else []

    tags = []
    for container in tag_containers:
        if container.text.strip().startswith("Tags:"):
            tag_links = container.find("span", class_="tags").find_all("a")
            tags = [a.find("span", class_="name").text for a in tag_links]
            break

    # extract page count
    page_count = None
    for container in tag_containers:
        if container.text.strip().startswith("Pages:"):
            span = container.find("span", class_="tags").find("a")
            page_count = int(span.find("span", class_="name").text)
            break

    # extract thumbnail
    cover_div = soup.find("div", id="cover")
    img_tag = cover_div.find("img") if cover_div else None
    thumbnail_url = img_tag.get("data-src") if img_tag else None

    result = {
        "id": gid,
        "title": id_to_title.get(gid, "N/A"),
        "tags": tags,
        "pages": page_count,
        "thumbnail_url": thumbnail_url
    }
    results.append(result)

driver.quit()

# ----------------------------
# Step 3: Telegram messages
# ----------------------------
with open(OLD_PATH, "r", encoding="utf-8") as f:
    past_data = json.load(f)

past_ids = {entry["id"] for entry in past_data}
new_galleries = [g for g in results if g["id"] not in past_ids]

import requests
for gallery in reversed(new_galleries):
    caption = (
        f"🆔 ID: {gallery['id']}\n\n"
        f"📛 Title: {gallery['title']}\n\n"
        f"🏷️ Tags: {', '.join(gallery['tags'])}\n\n"
        f"📄 Pages: {gallery['pages']}"
    )
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": CHAT_ID,
            "photo": gallery["thumbnail_url"],
            "caption": caption
        }
    )

# ----------------------------
# Step 4: Save results
# ----------------------------
with open(OLD_PATH, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
