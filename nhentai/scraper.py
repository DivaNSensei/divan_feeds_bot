from bs4 import BeautifulSoup
import requests, json

# main page popular titles and links scraping

url = "https://nhentai.net/"

response = requests.get(url)
page = BeautifulSoup(response.text, "html.parser")
parent_div = page.find("div", class_="container index-container index-popular")
galleries = parent_div.find_all("div", class_="gallery")
gallery_id = []
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
    gallery_id.append(gid)
    id_to_title[gid] = title ###

#################################################

# individual doujin's tags, page number and thumbnail link scraping

results = [] ###

for gid in gallery_id: ###
    url = f"https://nhentai.net/g/{gid}/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    tag_section = soup.find("section", id="tags")
    tag_containers = tag_section.find_all("div", class_=["tag-container", "field-name"])

    tags = []
    for container in tag_containers:
        if container.text.strip().startswith("Tags:"):
            tag_links = container.find("span", class_="tags").find_all("a")
            tags = [a.find("span", class_="name").text for a in tag_links]  ###
            break

    page_count = None
    for container in tag_containers:
        if container.text.strip().startswith("Pages:"):
            span = container.find("span", class_="tags").find("a")
            page_count = int(span.find("span", class_="name").text) ###
            break

    cover_div = soup.find("div", id="cover")
    img_tag = cover_div.find("img")
    thumbnail_url = img_tag.get("data-src") ###

    result = {
        "id": gid,
        "title": id_to_title[gid],
        "tags": tags,
        "pages": page_count,
        "thumbnail_url": thumbnail_url
    }
    results.append(result) ###

print(results)

with open("data/new.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)