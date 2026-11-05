import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_wallpapers as feed
import publish_wallpapers as publisher

REPO = "crisyov/crisyov-nothing-tv-wallpapers"
SEED = json.loads(Path(feed.__file__).with_name("seed_wallpapers.json").read_text(encoding="utf-8"))


def jpeg(width, height):
    return b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 8, 8, height, width, 1) + b"\xff\xd9"


class FeedTests(unittest.TestCase):
    def test_dimensions_and_bounds(self):
        self.assertEqual(feed.dimensions(jpeg(1280, 720))[:2], (1280, 720))
        self.assertEqual(feed.image(jpeg(780, 439)), "jpg")
        for width, height in ((3840, 2160), (1280, 721), (0, 720)):
            with self.assertRaises(ValueError):
                feed.image(jpeg(width, height))
        with self.assertRaises(ValueError):
            feed.image(b"not an image")

    def test_reject_original_urls_duplicates_and_nested_schema(self):
        row = dict(SEED[0])
        for url in ("https://image.tmdb.org/t/p/original/a.jpg", "http://image.tmdb.org/t/p/w780/a.jpg",
                    "https://user:pass@image.tmdb.org/t/p/w780/a.jpg", "https://example.com/image.jpg"):
            with self.assertRaises(ValueError):
                feed.validate([{**row, "imageUrl": url}], REPO)
        with self.assertRaises(ValueError):
            feed.validate([row, row], REPO)
        with self.assertRaises(ValueError):
            feed.validate([{**row, "secret": {"key": "fake"}}], REPO)

    def test_tmdb_filters_people_and_forces_size(self):
        def api(url, params, headers):
            if "/genre/" in url:
                return {"genres": [{"id": 878, "name": "Ciencia ficción"}]}
            return {"results": [{"media_type": "person", "id": 4, "backdrop_path": "/p.jpg"},
                    {"media_type": "movie", "id": 5, "title": "Example", "release_date": "2024-02-01",
                     "genre_ids": [878], "backdrop_path": "/film.jpg"}]}
        with patch.object(feed, "api", side_effect=api):
            rows = feed.tmdb("fake-key", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertIn("w1280", rows[0]["imageUrl"])
        self.assertIn("2024 • Ciencia ficción", rows[0]["subtitle"])
        feed.validate(rows, REPO)

    def test_rawg_crops_background_or_screenshot(self):
        results = {"results": [{"id": 4, "name": "Game", "released": "2020-01-02",
                                "short_screenshots": [{"image": "https://media.rawg.io/media/games/abc/demo.jpg"}],
                                "genres": [{"name": "Action"}]}]}
        with patch.object(feed, "api", return_value=results):
            rows = feed.rawg("fake-key")
        self.assertIn("/crop/1280/720/games/abc/demo.jpg", rows[0]["imageUrl"])
        feed.validate(rows, REPO)

    def test_pixabay_is_mirrored_and_never_requests_originals(self):
        results = {"hits": [{"id": 10, "webformatWidth": 640, "webformatHeight": 360,
                              "webformatURL": "https://cdn.pixabay.com/photo/demo_640.jpg", "user": "Author"},
                             {"id": 11, "webformatWidth": 4000, "webformatHeight": 2000,
                              "webformatURL": "https://cdn.pixabay.com/huge.jpg"}]}
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp)
            with patch.object(feed, "api", return_value=results), patch.object(feed, "request", return_value=jpeg(640, 360)) as request:
                rows = feed.pixabay("fake-key", REPO, media)
            self.assertEqual(len(rows), 1)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(rows[0]["imageUrl"].startswith("https://raw.githubusercontent.com/" + REPO + "/wallpapers/media/"))
            self.assertEqual(len(list(media.iterdir())), 1)
            feed.validate(rows, REPO)

    def test_no_keys_produces_explicit_preview_without_api_calls(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch.object(feed, "api") as api:
            path = Path(temp) / "wallpapers.json"
            rows = feed.generate(REPO, path, Path(temp) / "media")
            self.assertEqual(len(rows), 12)
            self.assertTrue(all("MUESTRA" in row["category"] for row in rows))
            self.assertFalse(api.called)
            self.assertEqual(feed.validate(json.loads(path.read_text(encoding="utf-8")), REPO), rows)

    def test_mirrored_images_are_verified_before_they_are_published(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PIXABAY_API_KEY": "fake-key"}, clear=True):
            media = Path(temp) / "media"
            media.mkdir()
            filename = "a" * 64 + ".jpg"
            (media / filename).write_bytes(jpeg(640, 360))
            row = feed.entry("pixabay-1", "FANTASÍA", "Demo", "Author • Pixabay",
                             f"https://raw.githubusercontent.com/{REPO}/wallpapers/media/{filename}")
            with patch.object(feed, "pixabay", return_value=[row]), patch.object(feed, "request", return_value=jpeg(780, 439)) as request:
                rows = feed.generate(REPO, Path(temp) / "wallpapers.json", media, verify=True)
            self.assertEqual(len(rows), 13)
            self.assertTrue(all("raw.githubusercontent.com" not in call.args[0] for call in request.call_args_list))

    def test_failed_provider_preserves_existing_output_and_hides_secret(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"TMDB_API_KEY": "fake-sensitive-key"}, clear=True):
            path = Path(temp) / "wallpapers.json"
            path.write_text("previous-valid-output")
            captured = io.StringIO()
            with patch.object(feed, "tmdb", side_effect=RuntimeError("fake-sensitive-key")), contextlib.redirect_stderr(captured):
                with self.assertRaises(RuntimeError):
                    feed.generate(REPO, path, Path(temp) / "media")
            self.assertEqual(path.read_text(), "previous-valid-output")
            self.assertNotIn("fake-sensitive-key", captured.getvalue())

    def test_verification_drops_oversized_images(self):
        counter = 0
        def image_request(*args, **kwargs):
            nonlocal counter
            counter += 1
            return jpeg(3840, 2160) if counter == 1 else jpeg(780, 439)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch.object(feed, "request", side_effect=image_request):
            rows = feed.generate(REPO, Path(temp) / "wallpapers.json", Path(temp) / "media", verify=True)
            self.assertEqual(len(rows), 11)


class PublicationTests(unittest.TestCase):
    def test_flat_branch_preserves_main_and_rejects_stale_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, work = root / "remote.git", root / "work"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            work.mkdir()
            before = Path.cwd()
            try:
                os.chdir(work)
                publisher.git("init", "-b", "main")
                publisher.git("config", "user.name", "Feed tests")
                publisher.git("config", "user.email", "tests@example.invalid")
                publisher.git("remote", "add", "origin", str(remote))
                Path("README.md").write_text("Source branch must remain unchanged")
                publisher.git("add", "README.md")
                publisher.git("commit", "-m", "Source")
                main = publisher.git("rev-parse", "HEAD")
                Path("wallpapers.json").write_text(json.dumps(SEED), encoding="utf-8")
                expected = Path("expected.txt")
                expected.write_text("")
                publisher.publish(REPO, expected)
                first = publisher.git("ls-remote", "origin", "refs/heads/wallpapers").split()[0]
                expected.write_text(first)
                updated = [dict(row) for row in SEED]
                updated[0]["subtitle"] += " • test"
                Path("wallpapers.json").write_text(json.dumps(updated), encoding="utf-8")
                publisher.publish(REPO, expected)
                second = publisher.git("ls-remote", "origin", "refs/heads/wallpapers").split()[0]
                self.assertNotEqual(first, second)
                self.assertEqual(publisher.git("rev-list", "--count", second), "1")
                self.assertEqual(publisher.git("rev-parse", "HEAD"), main)
                updated[0]["subtitle"] += " • concurrent"
                Path("wallpapers.json").write_text(json.dumps(updated), encoding="utf-8")
                with self.assertRaises(subprocess.CalledProcessError):
                    publisher.publish(REPO, expected)
            finally:
                os.chdir(before)


if __name__ == "__main__":
    unittest.main()
