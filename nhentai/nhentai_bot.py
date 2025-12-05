from dotenv import load_dotenv
import os, json, time, random
from bs4 import BeautifulSoup
import requests
from playwright.sync_api import sync_playwright
import datetime

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "nhentai", "data")
OLD_PATH = os.path.join(DATA_DIR, "old.json")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MAX_CAPTION_LENGTH = 1024  # Telegram caption limit

# Default User-Agent to present to remote sites (helps avoid simple bot blocks)
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/120.0.0.0 Safari/537.36"
)


def ensure_data_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass


def load_json_path(path):
    ensure_data_dir()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        ts = int(time.time())
        corrupt = f"{path}.corrupt.{ts}"
        try:
            os.replace(path, corrupt)
        except Exception:
            pass
        return []
    except Exception:
        return []


def save_json_path(path, data):
    ensure_data_dir()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        os.replace(tmp, path)

# ----------------------------
# Use a single Playwright browser/context for homepage + gallery pages
# This ensures Cloudflare cookies/challenges are shared between navigations
# ----------------------------
home_url = "https://nhentai.net/"
results = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=DEFAULT_UA)
    page = context.new_page()

    # fetch homepage
    try:
        page.goto(home_url, timeout=60000)
        time.sleep(2)
        hp_html = page.content()
    except Exception:
        try:
            page.goto(home_url, timeout=90000)
            time.sleep(3)
            hp_html = page.content()
        except Exception:
            hp_html = None

    if not hp_html:
        raise Exception("Failed to fetch homepage via Playwright")

    soup = BeautifulSoup(hp_html, "html.parser")
    parent_div = soup.find("div", class_="container index-container index-popular")
    if not parent_div:
        parent_div = soup.find("div", class_="index-popular")
    if not parent_div:
        ts = int(time.time())
        debug_name = f"homepage_debug_{ts}.html"
        debug_path = os.path.join(DATA_DIR, debug_name)
        try:
            ensure_data_dir()
            with open(debug_path, "w", encoding="utf-8") as fh:
                fh.write(hp_html or "")
        except Exception:
            debug_path = None
        snippet = (hp_html or "")[:2000]
        if debug_path:
            print(f"[nhentai_bot] Couldn't find popular container; saved homepage HTML to {debug_path}")
        else:
            print("[nhentai_bot] Couldn't find popular container; failed to save homepage HTML")
        print("[nhentai_bot] Snippet of received homepage (first 2000 chars):")
        print(snippet)
        raise Exception(f"Couldn't find the popular container on homepage; saved debug HTML to {debug_path}")

    galleries = parent_div.find_all("div", class_="gallery")
    print(f"[nhentai_bot] Found {len(galleries)} gallery elements on homepage")
    for g in galleries[:10]:
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        parts = [p for p in href.split('/') if p]
        gid = parts[-1] if parts else None
        title = (a.find("div", class_="caption") or a.find("span", class_="caption") or a).text.strip()
        print(f"  - {gid}: {title}")

    # collect gallery pages using same context (preserves cookies/challenge tokens)
    from urllib.parse import urljoin as _urljoin
    for gallery in galleries:
        a_tag = gallery.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag["href"]
        parts = [p for p in href.split('/') if p]
        gid = parts[-1] if parts else None
        if not gid:
            continue
        title = (a_tag.find("div", class_="caption") or a_tag.find("span", class_="caption") or a_tag).text.strip()
        gallery_url = f"https://nhentai.net/g/{gid}/"

        # try to extract thumbnail directly from the homepage listing (avoids visiting gallery page)
        homepage_thumbnail = None
        img_tag = gallery.select_one("a > img") or gallery.find("img")
        if img_tag:
            for attr in ("src", "data-src"):
                v = img_tag.get(attr)
                if v:
                    homepage_thumbnail = _urljoin("https://nhentai.net", v)
                    break
            if not homepage_thumbnail:
                ss = img_tag.get("srcset") or img_tag.get("data-srcset")
                if ss:
                    # pick the last candidate (usually highest resolution)
                    parts_ss = [p.strip() for p in ss.split(',') if p.strip()]
                    if parts_ss:
                        last = parts_ss[-1]
                        url_part = last.split()[0]
                        homepage_thumbnail = _urljoin("https://nhentai.net", url_part)

        # navigate to gallery using the same page/context
        try:
            page.goto(gallery_url, timeout=60000)
            # wait for cover or tags; give CF a chance to complete
            try:
                page.wait_for_selector("div#cover, section#tags", timeout=8000)
            except Exception:
                pass
            time.sleep(random.uniform(1, 2))
            page_html = page.content()
        except Exception:
            try:
                page.goto(gallery_url, timeout=90000)
                time.sleep(2)
                page_html = page.content()
            except Exception:
                page_html = None

        if not page_html:
            # could not fetch gallery; save debug and skip
            ts = int(time.time())
            debug_name = f"gallery_debug_{gid}_{ts}.html"
            debug_path = os.path.join(DATA_DIR, debug_name)
            try:
                ensure_data_dir()
                with open(debug_path, "w", encoding="utf-8") as fh:
                    fh.write("")
            except Exception:
                pass
            continue

        # parse gallery page content now (soup will be used later for thumbnail extraction)
        g_soup = BeautifulSoup(page_html, "html.parser") if page_html else None
        results.append((gid, title, page_html, g_soup, homepage_thumbnail))

    try:
        browser.close()
    except Exception:
        pass

# Build structured results from rendered gallery pages
from urllib.parse import urljoin

final_results = []
for gid, title, page_html, g_soup, homepage_thumbnail in results:
    # extract tags
    tag_section = g_soup.find("section", id="tags")
    tag_containers = tag_section.find_all("div", class_=["tag-container", "field-name"]) if tag_section else []

    tags = []
    for container in tag_containers:
        if container.text.strip().startswith("Tags:"):
            span_tags = container.find("span", class_="tags")
            if span_tags:
                tag_links = span_tags.find_all("a")
                tags = [ (a.find("span", class_="name").text if a.find("span", class_="name") else a.text.strip()) for a in tag_links ]
            break

    # extract page count
    page_count = None
    for container in tag_containers:
        if container.text.strip().startswith("Pages:"):
            span = container.find("span", class_="tags")
            if span:
                a = span.find("a")
                if a:
                    name_span = a.find("span", class_="name")
                    if name_span and name_span.text.isdigit():
                        page_count = int(name_span.text)
            break

    # extract thumbnail (prefer homepage thumbnail collected earlier)
    thumbnail_url = None
    if homepage_thumbnail:
        thumbnail_url = homepage_thumbnail
    else:
        thumbnail_url = None
    def pick_from_srcset(srcset):
        if not srcset:
            return None
        candidates = []
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            segments = part.split()
            url = segments[0]
            qualifier = segments[1] if len(segments) > 1 else ""
            weight = 0
            try:
                if qualifier.endswith("w"):
                    weight = int(qualifier[:-1])
                elif qualifier.endswith("x"):
                    weight = int(float(qualifier[:-1]) * 100)
            except Exception:
                weight = 0
            candidates.append((weight, url))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    thumbnail_url = thumbnail_url
    cover_div = g_soup.find("div", id="cover") if g_soup else None
    if cover_div:
        img_tag = cover_div.select_one("a > img") or cover_div.find("img")
        if img_tag:
            for attr in ("src", "data-src"):
                val = img_tag.get(attr)
                if val:
                    thumbnail_url = val
                    break
            if not thumbnail_url:
                for attr in ("srcset", "data-srcset"):
                    ss = img_tag.get(attr)
                    candidate = pick_from_srcset(ss)
                    if candidate:
                        thumbnail_url = candidate
                        break

    # fallback: open graph image meta
    if not thumbnail_url:
        meta_og = g_soup.find("meta", property="og:image")
        if meta_og and meta_og.get("content"):
            thumbnail_url = meta_og.get("content")

    # normalize to absolute URL
    if thumbnail_url:
        try:
            thumbnail_url = urljoin("https://nhentai.net", thumbnail_url)
        except Exception:
            pass
    else:
        ts = int(time.time())
        debug_name = f"gallery_debug_{gid}_{ts}.html"
        debug_path = os.path.join(DATA_DIR, debug_name)
        try:
            ensure_data_dir()
            with open(debug_path, "w", encoding="utf-8") as fh:
                fh.write(page_html or "")
            print(f"[nhentai_bot] Missing thumbnail for {gid}; saved page HTML to {debug_path}")
        except Exception:
            print(f"[nhentai_bot] Missing thumbnail for {gid}; failed to save debug HTML")

    final_results.append({
        "id": gid,
        "title": title,
        "tags": tags,
        "pages": page_count,
        "thumbnail_url": thumbnail_url,
    })

# replace results with structured final_results
results = final_results


# ----------------------------
# Step 4: Telegram messages for new galleries
# ----------------------------
ensure_data_dir()
past_data = load_json_path(OLD_PATH)

past_ids = {entry["id"] for entry in past_data}
new_galleries = [g for g in results if g["id"] not in past_ids]

print(f"[nhentai_bot] Parsed {len(results)} galleries; {len(new_galleries)} new galleries to send")
if len(new_galleries) == 0:
    print("[nhentai_bot] No new galleries found; exiting without sending messages")

for gallery in reversed(new_galleries):
    caption = (
        f"🆔 ID: {gallery['id']}\n\n"
        f"📛 Title: {gallery['title']}\n\n"
        f"🏷️ Tags: {', '.join(gallery['tags'])}\n\n"
        f"📄 Pages: {gallery['pages']}"
    )

    # Truncate if too long
    if len(caption) > MAX_CAPTION_LENGTH:
        caption = caption[:MAX_CAPTION_LENGTH - 3] + "..."

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "photo": gallery["thumbnail_url"],
                "caption": caption
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[nhentai_bot] Telegram send failed: {r.status_code} {r.text}")
        else:
            # mark as sent immediately to avoid duplicates if the process restarts
            try:
                past_data.append({
                    "id": gallery["id"],
                    "title": gallery["title"],
                    "tags": gallery.get("tags", []),
                    "pages": gallery.get("pages"),
                    "thumbnail_url": gallery.get("thumbnail_url"),
                })
                save_json_path(OLD_PATH, past_data)
            except Exception as e:
                print(f"[nhentai_bot] Warning: failed to update old.json after sending {gallery['id']}: {e}")
    except Exception as e:
        print(f"[nhentai_bot] Telegram request exception: {e}")


# ----------------------------
# Step 5: Save results
# ----------------------------
save_json_path(OLD_PATH, results)

