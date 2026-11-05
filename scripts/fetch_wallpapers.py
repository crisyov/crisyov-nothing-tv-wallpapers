#!/usr/bin/env python3
"""Generate a bounded flat wallpaper feed. API credentials never enter the output.

Without API secrets this publishes the explicitly labelled, curated preview catalog.
Pixabay's medium files are mirrored to the data branch rather than hotlinked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import struct
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

MAX_IMAGE = 3 * 1024 * 1024
MAX_MANIFEST = 256 * 1024
QUERIES = (("science fiction", "Ciencia ficción"), ("cyberpunk", "Cyberpunk"),
           ("fantasy", "Fantasía"), ("dark minimal", "Oscuro minimalista"))
FIELDS = {"id", "category", "title", "subtitle", "imageUrl"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url: str, *, headers=None, limit=2 * 1024 * 1024) -> bytes:
    if urlsplit(url).scheme != "https":
        raise ValueError("HTTPS is required")
    req = Request(url, headers={"User-Agent": "NothingTV-feed/0.4", **(headers or {})})
    for attempt in range(3):
        try:
            with build_opener(NoRedirect()).open(req, timeout=12) as response:
                length = int(response.headers.get("Content-Length", "-1"))
                if length > limit:
                    raise ValueError("Response exceeds size budget")
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Response exceeds size budget")
                return data
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                # Do not log request URLs: some providers carry credentials in the query.
                raise RuntimeError(f"Provider HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise RuntimeError("Provider connection failed") from None
        time.sleep(2 ** attempt)
    raise RuntimeError("Request exhausted")


def api(base: str, params=None, headers=None):
    return json.loads(request(base + ("?" + urlencode(params) if params else ""), headers=headers))


def dimensions(data: bytes) -> tuple[int, int, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 26:
        if data[25] not in (0, 2):
            raise ValueError("Only opaque PNGs are accepted")
        width, height = struct.unpack(">II", data[16:24])
        return width, height, "png"
    if data[:2] != b"\xff\xd8":
        raise ValueError("Only JPEG and opaque PNG are accepted")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 255:
            raise ValueError("Invalid JPEG marker")
        while i < len(data) and data[i] == 255:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            break
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 3:i + 7])
            return width, height, "jpg"
        i += length
    raise ValueError("JPEG dimensions not found")


def image(data: bytes) -> str:
    width, height, extension = dimensions(data)
    if len(data) > MAX_IMAGE or not (1 <= width <= 1280 and 1 <= height <= 720):
        raise ValueError("Image exceeds 1280x720 or byte budget")
    return extension


def sized_url(url: str, repository: str) -> bool:
    try:
        u = urlsplit(url)
        if u.scheme != "https" or u.username or u.password or u.port:
            return False
        if u.hostname == "image.tmdb.org":
            return bool(re.fullmatch(r"/t/p/w(?:780|1280)/[A-Za-z0-9_-]+\.(?:jpg|png)", u.path))
        if u.hostname == "media.rawg.io":
            return bool(re.fullmatch(r"/media/crop/1280/720/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png)", u.path)) and ".." not in u.path
        if u.hostname == "raw.githubusercontent.com":
            return bool(re.fullmatch(r"/" + re.escape(repository) + r"/wallpapers/media/[a-f0-9]{64}\.(?:jpg|png)", u.path))
    except ValueError:
        pass
    return False


def validate(rows, repository: str):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Catalog must contain 1–100 objects")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("Invalid flat catalog schema")
        for field, maximum in (("id", 128), ("category", 80), ("title", 240), ("subtitle", 240), ("imageUrl", 2048)):
            if not isinstance(row[field], str) or not row[field].strip() or len(row[field]) > maximum:
                raise ValueError("Invalid catalog field")
        if row["id"] in seen or not sized_url(row["imageUrl"], repository):
            raise ValueError("Duplicate ID or unsized image URL")
        seen.add(row["id"])
    if len(json.dumps(rows, ensure_ascii=False).encode()) > MAX_MANIFEST:
        raise ValueError("Catalog exceeds manifest size budget")
    return rows


def entry(id, category, title, subtitle, url):
    return dict(id=str(id), category=category, title=str(title)[:240],
                subtitle=subtitle[:240], imageUrl=url)


def tmdb(key: str, limit=20):
    # Supports either a v3 API key or the v4 read access token.
    headers = {"Authorization": f"Bearer {key}"} if len(key) > 40 else {}
    auth = {} if headers else {"api_key": key}
    genres = {}
    for kind in ("movie", "tv"):
        try:
            result = api(f"https://api.themoviedb.org/3/genre/{kind}/list", {**auth, "language": "es-MX"}, headers)
            genres.update({(kind, g["id"]): g["name"] for g in result.get("genres", [])})
        except (RuntimeError, ValueError, KeyError):
            pass
    rows = []
    for page in (1, 2):
        result = api("https://api.themoviedb.org/3/trending/all/week", {**auth, "language": "es-MX", "page": page}, headers)
        for item in result.get("results", []):
            kind, path = item.get("media_type"), item.get("backdrop_path")
            if kind not in ("movie", "tv") or not path:
                continue
            title = item.get("title") or item.get("name")
            if not title:
                continue
            year = (item.get("release_date") or item.get("first_air_date") or "Sin fecha")[:4]
            genre = next((genres[(kind, g)] for g in item.get("genre_ids", []) if (kind, g) in genres), "Cine" if kind == "movie" else "Series")
            rows.append(entry(f"tmdb-{kind}-{item['id']}", "PELÍCULAS" if kind == "movie" else "SERIES", title,
                              f"{year} • {genre} • TMDB", "https://image.tmdb.org/t/p/w1280" + path))
            if len(rows) >= limit:
                return rows
    return rows


def rawg(key: str, limit=15):
    result = api("https://api.rawg.io/api/games", {"key": key, "ordering": "-added,-rating", "page_size": 30})
    rows = []
    for item in result.get("results", []):
        source = item.get("background_image") or next((s.get("image") for s in item.get("short_screenshots", []) if s.get("image")), None)
        if not source or not item.get("name"):
            continue
        u = urlsplit(source)
        if u.scheme != "https" or u.hostname != "media.rawg.io" or not u.path.startswith("/media/"):
            continue
        relative = re.sub(r"^(?:crop|resize)/\d+/(?:\d+|-1)/", "", u.path[len("/media/"):])
        url = "https://media.rawg.io/media/crop/1280/720/" + relative
        year = (item.get("released") or "Sin fecha")[:4]
        genre = next((g.get("name") for g in item.get("genres", []) if g.get("name")), "Videojuegos")
        rows.append(entry(f"rawg-{item['id']}", "VIDEOJUEGOS", item["name"], f"{year} • {genre} • RAWG", url))
        if len(rows) >= limit:
            break
    return rows


def pixabay(key: str, repository: str, media: Path, limit=15):
    rows, seen = [], set()
    media.mkdir(parents=True, exist_ok=True)
    for query, theme in QUERIES:
        result = api("https://pixabay.com/api/", {"key": key, "q": query, "orientation": "horizontal",
                     "safesearch": "true", "per_page": 8, "order": "popular"})
        for item in result.get("hits", []):
            if item["id"] in seen or not (1 <= item.get("webformatWidth", 0) <= 1280 and 1 <= item.get("webformatHeight", 0) <= 720):
                continue
            source = item.get("webformatURL", "")
            u = urlsplit(source)
            if u.scheme != "https" or u.hostname not in ("pixabay.com", "cdn.pixabay.com"):
                continue
            try:
                data = request(source, limit=MAX_IMAGE)
                ext = image(data)
            except (RuntimeError, ValueError, OSError):
                continue
            filename = hashlib.sha256(data).hexdigest() + "." + ext
            (media / filename).write_bytes(data)
            seen.add(item["id"])
            url = f"https://raw.githubusercontent.com/{repository}/wallpapers/media/{filename}"
            rows.append(entry(f"pixabay-{item['id']}", theme.upper(), theme,
                              f"{item.get('user', 'Pixabay')} • {theme} • Pixabay", url))
            if len(rows) >= limit:
                return rows
            # Share the quota among all four themes rather than exhausting the first query.
            if len([r for r in rows if r["category"] == theme.upper()]) >= 4:
                break
    return rows


def generate(repository: str, output: Path, media: Path, previous: Path | None = None, verify=False):
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository) or repository.split("/")[1] in (".", ".."):
        raise ValueError("Invalid repository name")
    seed = validate(json.loads(Path(__file__).with_name("seed_wallpapers.json").read_text(encoding="utf-8")), repository)
    old = []
    if previous and previous.is_file():
        try:
            old = validate(json.loads(previous.read_text(encoding="utf-8")), repository)
        except (ValueError, OSError):
            print("Previous catalog invalid; retaining curated fallback only", file=sys.stderr)
    rows, configured, successes = [], 0, 0
    for name, secret, collector in (
        ("tmdb", "TMDB_API_KEY", lambda key: tmdb(key)),
        ("rawg", "RAWG_API_KEY", lambda key: rawg(key)),
        ("pixabay", "PIXABAY_API_KEY", lambda key: pixabay(key, repository, media)),
    ):
        key = os.environ.get(secret, "").strip()
        if not key:
            rows.extend(r for r in (old or seed) if r["id"].startswith(name + "-"))
            continue
        configured += 1
        try:
            found = collector(key)
            if not found:
                raise ValueError("Empty provider results")
            rows.extend(validate(found, repository))
            successes += 1
            print(f"{name}: {len(found)} objects")
        except (RuntimeError, ValueError, KeyError, TypeError, OSError):
            print(f"{name}: unavailable; preserving previous source entries", file=sys.stderr)
            rows.extend(r for r in old if r["id"].startswith(name + "-"))
    if configured and not successes:
        raise RuntimeError("All configured providers failed; published feed is untouched")
    if not configured:
        print("PREVIEW MODE: no API keys; curated samples are not weekly trends")
    # Unique IDs/URLs; the API order must never bias the displayed rotation.
    unique, urls = {}, set()
    for row in rows:
        if row["id"] not in unique and row["imageUrl"] not in urls:
            unique[row["id"]] = row
            urls.add(row["imageUrl"])
    rows = list(unique.values())[:50]
    random.Random(datetime.now(timezone.utc).date().isoformat()).shuffle(rows)
    rows = validate(rows, repository)
    if verify:
        checked = []
        for row in rows:
            try:
                local = media / urlsplit(row["imageUrl"]).path.rsplit("/", 1)[-1]
                if urlsplit(row["imageUrl"]).hostname == "raw.githubusercontent.com" and local.is_file():
                    image(local.read_bytes())
                else:
                    image(request(row["imageUrl"], limit=MAX_IMAGE))
                checked.append(row)
            except (RuntimeError, ValueError, OSError):
                print(f"Discarded invalid/unavailable image: {row['id']}", file=sys.stderr)
        rows = validate(checked, repository)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Published candidate: {len(rows)} objects, {output.stat().st_size} bytes")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "crisyov/crisyov-nothing-tv-wallpapers"))
    parser.add_argument("--output", type=Path, default=Path("wallpapers.json"))
    parser.add_argument("--media-dir", type=Path, default=Path("generated_media"))
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--verify-images", action="store_true")
    args = parser.parse_args()
    try:
        generate(args.repository, args.output, args.media_dir, args.previous, args.verify_images)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
        print(f"Generation failed ({type(error).__name__}); existing output is preserved", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
