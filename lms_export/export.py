"""Download Moodle course resources without publishing them in Git."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote, parse_qs

import requests
from bs4 import BeautifulSoup

BASE = "https://l.skolkovo.ru"
ROOT = Path(__file__).resolve().parent / "materials"


def safe_name(value):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip(" .")
    return value[:140] or "untitled"


class Client:
    def __init__(self):
        self.session = requests.Session()

    def get(self, url, **kwargs):
        # Validate every redirect before making the next request.
        for _ in range(10):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc != "l.skolkovo.ru":
                raise RuntimeError("Unexpected destination")
            response = self.session.get(url, allow_redirects=False, timeout=60, **kwargs)
            if response.is_redirect:
                url = urljoin(url, response.headers["Location"])
                response.close()
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("Too many redirects")

    def login(self):
        response = self.get(BASE + "/login/index.php")
        soup = BeautifulSoup(response.text, "html.parser")
        form = next(f for f in soup.select("form") if f.select_one('input[type="password"]'))
        data = {i["name"]: i.get("value", "") for i in form.select('input[type="hidden"][name]')}
        user = next(i for i in form.select("input[name]") if i.get("type", "text") in ("text", "email"))
        data[user["name"]] = os.environ["SKOLKOVO_EMAIL"]
        data[form.select_one('input[type="password"]')["name"]] = os.environ["SKOLKOVO_PASSWORD"]
        action = urljoin(response.url, form.get("action") or response.url)
        if urlsplit(action).netloc != "l.skolkovo.ru" or urlsplit(action).scheme != "https":
            raise RuntimeError("Unexpected login destination")
        result = self.session.post(action, data=data, allow_redirects=False, timeout=60)
        result.raise_for_status()
        # Authentication is checked on the requested course/overview page.
        # Avoid the potentially slow personalized dashboard.

    def page(self, url):
        response = self.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        if "/login/" in response.url or soup.select_one('input[type="password"]'):
            raise RuntimeError("LMS authentication required")
        return soup


def export(client, course_ids):
    failures = 0
    for course_id in course_ids:
        url = BASE + "/course/view.php?id=" + str(course_id)
        soup = client.page(url)
        title = soup.select_one("h1") or soup.title
        folder = ROOT / (str(course_id) + " - " + safe_name(title.get_text(" ", strip=True)))
        folder.mkdir(parents=True, exist_ok=True)
        queue = [(url, soup)]
        visited, files, records = set(), set(), []
        while queue:
            page_url, page = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            region = page.select_one("#region-main") or page
            # Keep the readable page text, including assignment instructions.
            digest = hashlib.sha256(page_url.encode()).hexdigest()[:12]
            (folder / ("page-" + digest + ".txt")).write_text(region.get_text("\n", strip=True), encoding="utf-8")
            links = [urljoin(page_url, a["href"]) for a in region.select("a[href]")]
            links += [urljoin(page_url, a["src"]) for a in region.select("iframe[src], embed[src]")]
            links += [urljoin(page_url, a["data"]) for a in region.select("object[data]")]
            for link in links:
                parsed = urlsplit(link)
                if parsed.scheme != "https" or parsed.netloc != "l.skolkovo.ru":
                    continue
                if "/pluginfile.php/" in parsed.path:
                    if link in files:
                        continue
                    files.add(link)
                    try:
                        with client.get(link, stream=True) as response:
                            if "text/html" in response.headers.get("Content-Type", "").lower():
                                raise RuntimeError("File returned HTML")
                            name = safe_name(unquote(parsed.path.rsplit("/", 1)[-1]))
                            target = folder / (hashlib.sha256(link.encode()).hexdigest()[:12] + " - " + name)
                            temp = target.with_name(target.name + ".part")
                            try:
                                size = 0
                                with temp.open("wb") as output:
                                    for chunk in response.iter_content(65536):
                                        size += len(chunk)
                                        if size > 200 * 1024 * 1024:
                                            raise RuntimeError("File exceeds 200 MB")
                                        output.write(chunk)
                                temp.replace(target)
                            finally:
                                temp.unlink(missing_ok=True)
                            records.append({"url": link, "file": target.name, "bytes": size})
                    except Exception:
                        failures += 1
                        records.append({"url": link, "error": "Download failed"})
                elif re.fullmatch(r"/mod/(resource|folder|page|book|assign|url)/view.php", parsed.path):
                    if link not in visited:
                        try:
                            queue.append((link, client.page(link)))
                        except Exception:
                            failures += 1
                            records.append({"url": link, "error": "Page unavailable"})
            if len(visited) > 2000:
                raise RuntimeError("Course page limit exceeded")
        (folder / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Course", course_id, "files:", len(files))
    return failures


def discover_courses(client):
    overview = client.page(BASE + "/grade/report/overview/index.php?id=1&userid=4643")
    region = overview.select_one("#region-main") or overview
    ids = set()
    for link in region.select("a[href]"):
        parsed = urlsplit(urljoin(BASE, link["href"]))
        if parsed.netloc != "l.skolkovo.ru":
            continue
        params = parse_qs(parsed.query)
        if parsed.path in ("/course/view.php", "/course/user.php"):
            value = params.get("id", [""])[0]
        elif parsed.path.startswith("/grade/report/") :
            value = params.get("id", [""])[0]
        else:
            continue
        if value.isdigit() and int(value) > 1:
            ids.add(int(value))
    if not ids:
        raise RuntimeError("No courses found in grade overview")
    print("Discovered courses:", len(ids))
    return sorted(ids)


def main():
    client = Client()
    client.login()
    print("LMS login completed")
    raw = os.environ.get("LMS_COURSE_IDS", "").strip()
    if raw:
        ids = [int(value) for value in re.split(r"[,\s]+", raw) if value]
        if not ids or any(value <= 0 for value in ids):
            raise RuntimeError("Invalid course IDs")
    else:
        ids = discover_courses(client)
    failures = export(client, ids)
    print("Failed resources:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Export failed:", type(error).__name__)
        if isinstance(error, RuntimeError):
            print(str(error))
        sys.exit(1)
