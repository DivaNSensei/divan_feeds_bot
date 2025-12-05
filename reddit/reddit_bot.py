import os
import json
import logging
import requests
import praw
import html
from dotenv import load_dotenv
import tempfile
import shutil
import mimetypes
from urllib.parse import unquote, urlparse
import base64
from datetime import datetime
import time

try:
    import yt_dlp
except Exception:
    yt_dlp = None
try:
    import redgifs
except Exception:
    redgifs = None
 
load_dotenv()
# Respect LOG_LEVEL environment variable (default INFO) so we can enable debug output during troubleshooting
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# === Config ===
SUBREDDIT = "gonewildaudio"
SUBREDDIT_POST_LIMIT = 50

MULTIREDDIT_NAME = "lewds"
MULTIREDDIT_POST_LIMIT = 100
MULTIREDDIT_SORT = "hot"
DELAY_SECONDS = 1
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "reddit", "data")
OLD_FILE = os.path.join(DATA_DIR, "reddit_old.json")
SEEN_FILE = os.path.join(DATA_DIR, "reddit_seen.json")
MULTIREDDIT_SEEN_FILE = os.path.join(DATA_DIR, "reddit_multireddit_seen.json")
MULTIREDDIT_OLD_FILE = os.path.join(DATA_DIR, "reddit_multireddit_old.json")

# Clear JSON state files at startup to allow resending during tests
# Default: clear multireddit files each run to help manual testing
# Manual override flags (rarely used). Default: do NOT clear on every run.
CLEAR_ALL_SEEN_ON_START = os.getenv("CLEAR_ALL_SEEN_ON_START", "false").lower() in ("1","true","yes")
CLEAR_MULTIREDDIT_ON_START = os.getenv("CLEAR_MULTIREDDIT_ON_START", "false").lower() in ("1","true","yes")

# Automatic clear-on-code-change behaviour
# If enabled, the bot will clear seen/old JSON files when the repo commit hash changes.
# Default: do NOT clear on code change unless explicitly enabled via env var.
CLEAR_ON_CODECHANGE = os.getenv("CLEAR_ON_CODECHANGE", "false").lower() in ("1","true","yes")
# If true, clear both subreddit and multireddit files on code change. Otherwise clear only multireddit files.
CLEAR_ALL_ON_CODECHANGE = os.getenv("CLEAR_ALL_ON_CODECHANGE", "false").lower() in ("1","true","yes")

# file to persist last seen commit hash
LAST_COMMIT_FILE = os.path.join(DATA_DIR if 'DATA_DIR' in globals() else ".", "last_code_hash.txt")
 
# Whether to allow redgifs downloads. Default true (via redgifs API).
ALLOW_REDGIFS = os.getenv("ALLOW_REDGIFS", "true").lower() in ("1","true","yes")

# === Load Secrets ===
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# === Init Reddit ===
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    username=REDDIT_USERNAME,
    password=REDDIT_PASSWORD,
    user_agent=REDDIT_USER_AGENT
)

# reuse a requests session for Telegram and downloads
session = requests.Session()

# === Helper Functions ===

def safe_filename_from_url(url):
    path = urlparse(url).path
    name = os.path.basename(path) or "file"
    name = unquote(name)
    # add extension if missing
    if not os.path.splitext(name)[1]:
        ext = mimetypes.guess_extension(session.head(url, allow_redirects=True).headers.get("content-type", "").split(";")[0])
        if ext:
            name += ext
    return name

def download_media(url, max_bytes=50 * 1024 * 1024):
    """Download media to a temp file. Returns (path, content_type, size) or (None, None, 0) on failure/too large."""
    try:
        resp = session.get(url, stream=True, timeout=30, headers={"User-Agent": REDDIT_USER_AGENT or "reddit-bot"})
    except requests.RequestException as e:
        logger.exception("Download request failed for %s: %s", url, e)
        return None, None, 0

    if resp.status_code != 200:
        logger.error("Failed to download %s: status %s", url, resp.status_code)
        return None, None, 0
    content_type_header = (resp.headers.get("Content-Type") or "").lower()
    # If server returns an HTML page (common for embed/watch pages), skip here
    if content_type_header.startswith("text/html") or content_type_header.startswith("application/xhtml+xml"):
        logger.info("Remote URL %s returned HTML content-type (%s); skipping raw download so fallback extractors can run", url, content_type_header)
        return None, content_type_header, 0

    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        logger.info("Remote file too large (%s bytes) for upload limit", content_length)
        return None, resp.headers.get("Content-Type"), int(content_length)

    tmp_dir = tempfile.mkdtemp(prefix="reddit_media_")
    filename = safe_filename_from_url(url)
    tmp_path = os.path.join(tmp_dir, filename)
    total = 0
    try:
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    logger.info("Downloaded size exceeded max (%d bytes). Aborting.", total)
                    f.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return None, resp.headers.get("Content-Type"), total
                f.write(chunk)
        return tmp_path, resp.headers.get("Content-Type"), total
    except Exception:
        logger.exception("Error while saving media from %s", url)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, None, 0

def ytdlp_download(url, max_bytes=50 * 1024 * 1024):
    """Use yt-dlp to download a URL into a temp directory. Returns (path, content_type, size) or (None,None,0)."""
    if not yt_dlp:
        logger.warning("yt-dlp not available; skipping ytdlp download for %s", url)
        return None, None, 0

    tmp_dir = tempfile.mkdtemp(prefix="ytdlp_media_")
    ydl_opts = {
        'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
    }
    # support cookies via env var: path or raw content
    cookies_path = os.getenv('YTDLP_COOKIES_PATH')
    cookies_content = os.getenv('YTDLP_COOKIES_CONTENT')
    temp_cookie_file = None
    if cookies_content and not cookies_path:
        # write content to a temp cookie file
        temp_cookie_file = os.path.join(tmp_dir, 'cookies.txt')
        try:
            with open(temp_cookie_file, 'w', encoding='utf-8') as cf:
                cf.write(cookies_content)
            cookies_path = temp_cookie_file
        except Exception:
            logger.exception('Failed to write yt-dlp cookie content to temp file')
            cookies_path = None
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # info may be a dict or list; handle dict
            if not info:
                return None, None, 0
            # find downloaded filename: check _filename or use id/ext
            if isinstance(info, dict):
                fn = ydl.prepare_filename(info)
            else:
                fn = None
            # if file exists, return it
            if fn and os.path.exists(fn):
                size = os.path.getsize(fn)
                if size > max_bytes:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return None, None, size
                ctype, _ = mimetypes.guess_type(fn)
                return fn, ctype, size
    except Exception:
        logger.exception("yt-dlp failed to download %s", url)
    # nothing found
    # cleanup cookie file if we created one
    if temp_cookie_file and os.path.exists(temp_cookie_file):
        try:
            os.remove(temp_cookie_file)
        except Exception:
            pass
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return None, None, 0

def redgifs_download(url, max_bytes=50 * 1024 * 1024):
    """Extract and download a Redgifs video using the redgifs API. Returns (path, content_type, size) or (None,None,0)."""
    if not redgifs:
        logger.warning("redgifs library not available; skipping redgifs download for %s", url)
        return None, None, 0

    tmp_dir = None
    api = None
    keep_tmp = False
    try:
        # Extract ID from URL: https://redgifs.com/watch/abcxyz or https://www.redgifs.com/watch/abcxyz
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) < 2 or path_parts[0] not in ('watch', 'gifs'):
            logger.warning("Could not extract Redgifs ID from %s", url)
            return None, None, 0
        gif_id = path_parts[1]

        # Initialize and login to Redgifs API
        api = redgifs.API()
        api.login()
        
        # Fetch the GIF details
        gif = api.get_gif(gif_id)
        if not gif or not gif.urls:
            logger.warning("Could not fetch Redgifs GIF details for ID %s", gif_id)
            api.close()
            return None, None, 0

        # Build a prioritized list of candidate direct URLs
        urls = []
        try:
            u = gif.urls
            # helper to extract attribute or dict key
            def ex(k):
                try:
                    return getattr(u, k)
                except Exception:
                    try:
                        return u.get(k)
                    except Exception:
                        return None

            # Try the common fields and some alternates
            candidates = [
                'file_url', 'mp4', 'mp4_url', 'mp4Url', 'mp4UrlHttps',
                'hd', 'hd_url', 'hd_mp4', 'sd', 'sd_url', 'gifv',
                'embed_url', 'web_url', 'webm', 'poster'
            ]
            for k in candidates:
                v = ex(k)
                if v:
                    # normalize strings and add unique entries
                    if isinstance(v, str):
                        if v not in urls:
                            urls.append(v)
                    elif isinstance(v, (list, tuple)):
                        for item in v:
                            if item and item not in urls:
                                urls.append(item)
        except Exception:
            urls = []

        if not urls:
            logger.warning("No candidate URLs available for Redgifs GIF %s", gif_id)
            api.close()
            return None, None, 0

        # debug: list candidates
        logger.debug("Redgifs candidates for %s: %s", gif_id, urls)

        tmp_dir = tempfile.mkdtemp(prefix="redgifs_media_")
        tmp_path = os.path.join(tmp_dir, f"{gif_id}.mp4")

        # Try each candidate URL until we get a non-HTML media file
        for candidate in urls:
            if not candidate:
                continue
            try:
                # Inspect candidate first with a lightweight HEAD request to avoid HTML pages
                logger.info("Trying Redgifs candidate URL for %s: %s", gif_id, candidate)
                try:
                    head_resp = session.head(candidate, allow_redirects=True, timeout=10, headers={"User-Agent": REDDIT_USER_AGENT or "reddit-bot"})
                    head_ct = (head_resp.headers.get("Content-Type") or "").lower()
                    head_len = head_resp.headers.get("Content-Length")
                except Exception:
                    head_resp = None
                    head_ct = ""
                    head_len = None

                logger.debug("Candidate HEAD for %s -> content-type=%s, content-length=%s", candidate, head_ct, head_len)

                # If the head indicates HTML, skip this candidate (it's likely a watch/embed page)
                if head_ct.startswith("text/html") or "<html" in candidate.lower() or "/watch/" in candidate.lower():
                    logger.info("Skipping candidate %s because it appears to be an HTML page (content-type=%s)", candidate, head_ct)
                    continue

                # If the server advertises a content-length above our limit, skip/return
                if head_len:
                    try:
                        if int(head_len) > max_bytes:
                            logger.info("Remote file too large (head content-length=%s) for upload limit", head_len)
                            api.close()
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                            return None, None, int(head_len)
                    except Exception:
                        pass

                # Use the library download helper where possible (after basic head checks)
                api.download(candidate, tmp_path)

                if not os.path.exists(tmp_path):
                    continue

                # quick sanity check: detect HTML saved instead of media
                try:
                    with open(tmp_path, 'rb') as fh:
                        head = fh.read(1024)
                except Exception:
                    head = b''

                # if file looks like HTML, skip
                try:
                    head_text = head.decode('utf-8', errors='ignore').lower()
                except Exception:
                    head_text = ''
                if '<!doctype' in head_text or '<html' in head_text or '<script' in head_text:
                    logger.warning("Redgifs candidate %s returned HTML (not media); trying next", candidate)
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    continue

                size = os.path.getsize(tmp_path)
                if size > max_bytes:
                    logger.info("Redgifs video too large (%d bytes) for upload limit", size)
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    api.close()
                    return None, None, size

                logger.info("Downloaded Redgifs video %s (%d bytes) from candidate", gif_id, size)
                # caller will take ownership of the downloaded file — don't delete it here
                keep_tmp = True
                return tmp_path, "video/mp4", size
            except Exception:
                logger.exception("Failed to download Redgifs candidate %s for %s", candidate, gif_id)
                # remove possibly-bad file and continue
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        # nothing worked
        # fall through to cleanup below
    except Exception:
        logger.exception("Redgifs extraction failed for %s", url)
    finally:
        # ensure resources are cleaned when we did not return a file
        try:
            if api:
                try:
                    api.close()
                except Exception:
                    pass
        except Exception:
            pass
        # if tmp_dir exists but we did not return, remove it (if we returned we set keep_tmp=True)
        try:
            # if we returned earlier, tmp_dir still exists but caller will remove it
            # only remove tmp_dir if it still contains the mp4 and we didn't return (no mp4 created)
            if tmp_dir and os.path.exists(tmp_dir) and not keep_tmp:
                # if directory is empty or contains non-media, safe to remove
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
    return None, None, 0

def telegram_send_file(file_path, file_field, method, extra_data):
    """Upload a single file to Telegram using multipart/form-data.
    file_field: 'photo', 'video', 'animation', or 'document'
    method: API method name, e.g., 'sendPhoto' (without base URL)
    extra_data: dict of other form fields (chat_id, caption, parse_mode, etc.)
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        with open(file_path, "rb") as fh:
            files = {file_field: fh}
            r = requests.post(url, data=extra_data, files=files, timeout=60)
        if r.status_code != 200:
            logger.error("Telegram %s returned %s: %s", method, r.status_code, r.text)
            return False
        return True
    except Exception:
        logger.exception("Failed to upload file to Telegram")
        return False


def send_album(paths, post, source):
    """Send multiple images as an album (media group). paths: list of file paths."""
    if not paths:
        return False
    # Telegram allows up to 10 media in an album
    items = []
    files = {}
    for i, path in enumerate(paths[:10]):
        attach = f'file{i}'
        # all photos for album
        item = {"type": "photo", "media": f"attach://{attach}"}
        # add caption only to first item
        if i == 0:
            item["caption"] = f"<b>{html.escape(post['title'])}</b>\n👤 by <code>{html.escape(post['author'])}</code>\n👍 {post['score']} upvotes\n<code>{html.escape(post['permalink'])}</code>"
            item["parse_mode"] = "HTML"
        items.append(item)
        files[attach] = open(path, 'rb')

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    data = {"chat_id": CHAT_ID, "media": json.dumps(items)}
    try:
        r = requests.post(url, data=data, files=files, timeout=60)
        if r.status_code != 200:
            logger.error("sendMediaGroup returned %s: %s", r.status_code, r.text)
            return False
        logger.info("Uploaded album for %s post %s", source, post['id'])
        return True
    except Exception:
        logger.exception("Failed to upload album to Telegram")
        return False
    finally:
        for f in files.values():
            try:
                f.close()
            except Exception:
                pass
        # cleanup paths directory
        try:
            shutil.rmtree(os.path.dirname(paths[0]), ignore_errors=True)
        except Exception:
            pass

def send_media(post, media_url, mime, source):
    """Download media and upload to Telegram. Returns True on success."""
    if not media_url:
        return False
    
    # first try a simple direct download (may return HTML if it's a link page)
    path, content_type, size = download_media(media_url)
    download_source = 'direct'
    # If direct download returned a file, sanity-check it for HTML even when headers lied
    if path:
        try:
            # read a small head to check for HTML
            with open(path, 'rb') as fh:
                head = fh.read(2048)
            head_text = head.decode('utf-8', errors='ignore').lower()
        except Exception:
            head_text = ''

        if '<!doctype' in head_text or '<html' in head_text or '<script' in head_text:
            logger.info("Direct download of %s resulted in HTML content — rejecting file and trying extractors", media_url)
            try:
                shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            except Exception:
                pass
            path = None

    if not path:
        # Try Redgifs extraction if it's a Redgifs URL and library available
        host = urlparse(media_url).netloc.lower()
        if ("redgifs" in host or "redgif" in host) and ALLOW_REDGIFS:
            logger.info("Attempting Redgifs extraction for %s", media_url)
            path, content_type, size = redgifs_download(media_url)
            download_source = 'redgifs'
        
        # Fallback to yt-dlp for other complex hosts
        if not path:
            # try yt-dlp as a final fallback
            logger.info("Trying yt-dlp fallback for %s", media_url)
            path, content_type, size = ytdlp_download(media_url)
            download_source = 'yt-dlp'
        
        if not path:
            # either too large (size set) or download failed
            if size and size > 0:
                logger.info("Media too large to upload (%d bytes). Falling back to sending link.", size)
            return False

    # decide send method based on mime/type or extension
    ct = content_type or mimetypes.guess_type(path)[0] or ""
    lower_ct = ct.lower()
    extra = {
        "chat_id": CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    caption = f"<b>{html.escape(post['title'])}</b>\n👤 by <code>{html.escape(post['author'])}</code>\n👍 {post['score']} upvotes\n<code>{html.escape(post['permalink'])}</code>"
    extra["caption"] = caption

    success = False

    logger.debug("Uploading media (source=%s) path=%s content_type=%s size=%s", download_source, path, ct, size)

    # Try appropriate send method. If an image fails (PHOTO_INVALID_DIMENSIONS),
    # try sending as a document as a fallback.
    try:
        if lower_ct.startswith("image/"):
            if lower_ct == "image/gif":
                success = telegram_send_file(path, "animation", f"sendAnimation", extra)
            else:
                success = telegram_send_file(path, "photo", f"sendPhoto", extra)
            if not success:
                logger.info("Image upload failed for post %s; trying as document", post['id'])
                success = telegram_send_file(path, "document", f"sendDocument", extra)
        elif lower_ct.startswith("video/"):
            success = telegram_send_file(path, "video", f"sendVideo", extra)
        else:
            # Unknown type -> try sendDocument
            success = telegram_send_file(path, "document", f"sendDocument", extra)
    except Exception:
        logger.exception("Exception while sending media for post %s", post['id'])
    finally:
        # cleanup
        try:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        except Exception:
            pass

    if not success:
        logger.error("Failed to send media for post %s; falling back to link", post['id'])
    else:
        logger.info("Uploaded media for %s post %s", source, post['id'])
    return success

def send_telegram(post, source="subreddit"):
    """Send a post to Telegram. Try to upload media if available, otherwise send formatted text/link."""
    # Basic text (used as fallback caption too)
    text = (
        f"<b>{html.escape(post['title'])}</b>\n"
        f"👤 by <code>{html.escape(post['author'])}</code>\n"
        f"👍 {post['score']} upvotes\n"
    )
    if "subreddit" in post:
        text += f"📍 r/{html.escape(post['subreddit'])}\n"
    text += f"<code>{html.escape(post['permalink'])}</code>"

    if not BOT_TOKEN or not CHAT_ID:
        logger.error("BOT_TOKEN or CHAT_ID not set; cannot send message")
        return False

    # Attempt to detect media info on the post dict
    # priority: reddit video -> gallery -> direct url
    media_sent = False
    try:
        # reddit hosted video
        if post.get("is_video") and post.get("video_url"):
            media_sent = send_media(post, post["video_url"], "video/mp4", source)

        # gallery: try to download multiple images and send as an album
        elif post.get("is_gallery") and post.get("gallery_urls"):
            paths = []
            for url in post["gallery_urls"][:10]:
                pth, ctype, sz = download_media(url)
                if not pth:
                    pth, ctype, sz = ytdlp_download(url)
                if pth:
                    paths.append(pth)
            if paths:
                media_sent = send_album(paths, post, source)

        # direct link (image or gif)
        elif post.get("url"):
            media_sent = send_media(post, post["url"], None, source)
    except Exception:
        logger.exception("Error while attempting to send media for post %s", post.get("id"))

    if media_sent:
        return True

    # fallback: send text message with link
    url_api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = session.post(url_api, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error("Telegram API returned %s for %s post %s: %s", r.status_code, source, post['id'], r.text)
            return False
        logger.info("Sent %s post %s to Telegram (link)", source, post['id'])
        return True
    except requests.RequestException:
        logger.exception("Failed to send %s post to Telegram", source)
        return False


def fetch_posts():
    posts = []
    try:
        for post in reddit.subreddit(SUBREDDIT).top(time_filter="week", limit=SUBREDDIT_POST_LIMIT):
            if "f4" in post.title.lower():
                p = {
                    "id": post.id,
                    "title": post.title,
                    "author": post.author.name if post.author else "[deleted]",
                    "score": post.score,
                    "permalink": f"https://reddit.com{post.permalink}",
                    "url": getattr(post, "url", None),
                    "is_video": getattr(post, "is_video", False),
                }
                # reddit hosted video
                if p["is_video"] and getattr(post, "media", None):
                    try:
                        video = post.media.get("reddit_video", {})
                        p["video_url"] = video.get("fallback_url")
                    except Exception:
                        p["video_url"] = None

                # gallery
                if getattr(post, "is_gallery", False):
                    try:
                        meta = getattr(post, "media_metadata", {})
                        gallery = []
                        for k in getattr(post, "gallery_data", {}).get("items", []):
                            key = k.get("media_id")
                            m = meta.get(key, {})
                            # prefer 's' -> 'u'
                            u = m.get("s", {}).get("u")
                            if u:
                                gallery.append(html.unescape(u).replace("&amp;", "&"))
                        p["is_gallery"] = True
                        p["gallery_urls"] = gallery
                    except Exception:
                        p["is_gallery"] = False
                        p["gallery_urls"] = []
                posts.append(p)
        logger.info("Fetched %d posts from r/%s matching 'f4'", len(posts), SUBREDDIT)
    except Exception as e:
        logger.error("Failed to fetch posts from r/%s: %s", SUBREDDIT, e)
    return posts


def fetch_multireddit_posts(multireddit_name, filter_keyword=None):
    posts = []
    try:
        multi = None
        for m in reddit.user.multireddits():
            if m.name.lower() == multireddit_name.lower():
                multi = m
                break

        if not multi:
            logger.warning("Multireddit '%s' not found for user", multireddit_name)
            return posts

        sort_func = getattr(multi, MULTIREDDIT_SORT, None)
        if not callable(sort_func):
            sort_func = multi.hot

        for post in sort_func(limit=MULTIREDDIT_POST_LIMIT):
            if filter_keyword and filter_keyword.lower() not in post.title.lower():
                continue
            p = {
                "id": post.id,
                "title": post.title,
                "author": post.author.name if post.author else "[deleted]",
                "score": post.score,
                "permalink": f"https://reddit.com{post.permalink}",
                "subreddit": post.subreddit.display_name,
                "url": getattr(post, "url", None),
                "is_video": getattr(post, "is_video", False),
            }
            if p["is_video"] and getattr(post, "media", None):
                try:
                    video = post.media.get("reddit_video", {})
                    p["video_url"] = video.get("fallback_url")
                except Exception:
                    p["video_url"] = None

            if getattr(post, "is_gallery", False):
                try:
                    meta = getattr(post, "media_metadata", {})
                    gallery = []
                    for k in getattr(post, "gallery_data", {}).get("items", []):
                        key = k.get("media_id")
                        m = meta.get(key, {})
                        u = m.get("s", {}).get("u")
                        if u:
                            gallery.append(html.unescape(u).replace("&amp;", "&"))
                    p["is_gallery"] = True
                    p["gallery_urls"] = gallery
                except Exception:
                    p["is_gallery"] = False
                    p["gallery_urls"] = []

            # Exclude posts from r/gonewildaudio (handled elsewhere)
            try:
                if p.get("subreddit", "").lower() == "gonewildaudio":
                    logger.debug("Skipping post %s from r/gonewildaudio", p["id"])
                    continue
            except Exception:
                pass

            posts.append(p)
        logger.info("Fetched %d posts from multireddit '%s' (sort=%s)", len(posts), multireddit_name, MULTIREDDIT_SORT)
    except praw.exceptions.APIException as e:
        logger.error("Reddit API exception fetching multireddit '%s': %s", multireddit_name, e)
    except Exception as e:
        logger.exception("Failed to fetch posts from multireddit '%s': %s", multireddit_name, e)
    return posts


def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("Failed to decode JSON from %s: %s", path, e)
        # Move corrupt file aside so we don't repeatedly crash; start fresh
        try:
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            corrupt_path = f"{path}.corrupt.{ts}"
            os.replace(path, corrupt_path)
            logger.warning("Moved corrupt JSON to %s and starting with an empty list", corrupt_path)
        except Exception:
            logger.exception("Failed to move corrupt JSON file %s", path)
        return []


def save_json(path, data):
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.exception("Failed to write JSON to %s", path)


def main():
    # ensure data dir exists
    os.makedirs(DATA_DIR, exist_ok=True)
    # Clear JSON state files only when appropriate:
    # - If manual override env vars are set (CLEAR_ALL_SEEN_ON_START or CLEAR_MULTIREDDIT_ON_START)
    # - Otherwise, if CLEAR_ON_CODECHANGE is enabled and the repo commit hash changed since last run
    try:
        if CLEAR_ALL_SEEN_ON_START:
            logger.info("Clearing all seen/old JSON files at startup (CLEAR_ALL_SEEN_ON_START)")
            save_json(SEEN_FILE, [])
            save_json(OLD_FILE, [])
            save_json(MULTIREDDIT_SEEN_FILE, [])
            save_json(MULTIREDDIT_OLD_FILE, [])
        elif CLEAR_MULTIREDDIT_ON_START:
            logger.info("Clearing multireddit seen/old JSON files at startup (CLEAR_MULTIREDDIT_ON_START)")
            save_json(MULTIREDDIT_SEEN_FILE, [])
            save_json(MULTIREDDIT_OLD_FILE, [])
        else:
            # Try code-change detection via git commit hash
            if CLEAR_ON_CODECHANGE:
                try:
                    import subprocess
                    cur = None
                    try:
                        p = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True, check=True)
                        cur = p.stdout.strip()
                    except Exception:
                        cur = None

                    prev = None
                    if os.path.exists(LAST_COMMIT_FILE):
                        try:
                            prev = open(LAST_COMMIT_FILE, 'r', encoding='utf-8').read().strip()
                        except Exception:
                            prev = None

                    if cur and cur != prev:
                        logger.info("Repository commit changed (prev=%s cur=%s). Clearing JSON files as configured.", prev, cur)
                        # Clear either all or only multireddit files
                        if CLEAR_ALL_ON_CODECHANGE:
                            save_json(SEEN_FILE, [])
                            save_json(OLD_FILE, [])
                        save_json(MULTIREDDIT_SEEN_FILE, [])
                        save_json(MULTIREDDIT_OLD_FILE, [])
                        try:
                            with open(LAST_COMMIT_FILE, 'w', encoding='utf-8') as fh:
                                fh.write(cur)
                        except Exception:
                            logger.exception('Failed to write last commit file')
                except Exception:
                    logger.exception('Error while checking git commit for code-change clearing')
    except Exception:
        logger.exception("Failed while attempting to clear JSON files at startup")

    # === Process subreddit posts ===
    logger.info("=== Processing subreddit: %s ===", SUBREDDIT)
    new_posts = fetch_posts()
    seen = set(load_json(SEEN_FILE))
    fresh_posts = [p for p in new_posts if p['id'] not in seen]

    if not fresh_posts:
        logger.info("No new posts to send from r/%s", SUBREDDIT)
    else:
        logger.info("Found %d new posts from r/%s", len(fresh_posts), SUBREDDIT)

    sent_any = False
    for post in fresh_posts:
        ok = send_telegram(post, source="subreddit")
        if ok:
            seen.add(post['id'])
            sent_any = True
        else:
            logger.error("Failed to send subreddit post %s", post['id'])
        # throttle between sends to avoid hitting Telegram rate limits
        try:
            time.sleep(DELAY_SECONDS)
        except Exception:
            pass

    if sent_any or not os.path.exists(SEEN_FILE):
        seen_list = list(seen)
        MAX_SEEN = 10000
        if len(seen_list) > MAX_SEEN:
            seen_list = seen_list[-MAX_SEEN:]
        save_json(SEEN_FILE, seen_list)

    save_json(OLD_FILE, new_posts)

    # === Process multireddit posts ===
    logger.info("=== Processing multireddit: %s ===", MULTIREDDIT_NAME)
    multi_posts = fetch_multireddit_posts(MULTIREDDIT_NAME)
    multi_seen = set(load_json(MULTIREDDIT_SEEN_FILE))
    multi_fresh_posts = [p for p in multi_posts if p['id'] not in multi_seen]

    if not multi_fresh_posts:
        logger.info("No new posts to send from multireddit '%s'", MULTIREDDIT_NAME)
    else:
        logger.info("Found %d new posts from multireddit '%s'", len(multi_fresh_posts), MULTIREDDIT_NAME)

    multi_sent_any = False
    for post in multi_fresh_posts:
        ok = send_telegram(post, source="multireddit")
        if ok:
            multi_seen.add(post['id'])
            multi_sent_any = True
        else:
            logger.error("Failed to send multireddit post %s", post['id'])
        # throttle between sends to avoid hitting Telegram rate limits
        try:
            time.sleep(DELAY_SECONDS)
        except Exception:
            pass

    if multi_sent_any or not os.path.exists(MULTIREDDIT_SEEN_FILE):
        multi_seen_list = list(multi_seen)
        MAX_SEEN = 10000
        if len(multi_seen_list) > MAX_SEEN:
            multi_seen_list = multi_seen_list[-MAX_SEEN:]
        save_json(MULTIREDDIT_SEEN_FILE, multi_seen_list)

    save_json(MULTIREDDIT_OLD_FILE, multi_posts)


if __name__ == "__main__":
    main()
