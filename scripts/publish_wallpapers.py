#!/usr/bin/env python3
"""Publish generated data as one parentless commit; never rewrite the source branch."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit
from fetch_wallpapers import validate


def git(*args, input=None, env=None):
    return subprocess.check_output(["git", *args], input=input, env=env).decode().strip()


def publish(repository, expected_file):
    rows = validate(json.loads(Path("wallpapers.json").read_text(encoding="utf-8")), repository)
    expected = expected_file.read_text().strip()
    with tempfile.TemporaryDirectory(prefix="nothingtv-feed-") as temp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index")}
        git("read-tree", "--empty", env=env)
        paths = [(Path("wallpapers.json"), "wallpapers.json")]
        for row in rows:
            u = urlsplit(row["imageUrl"])
            if u.hostname == "raw.githubusercontent.com":
                filename = u.path.rsplit("/", 1)[1]
                source = Path("generated_media") / filename
                if not source.is_file():
                    raise ValueError("A referenced mirrored image is missing")
                paths.append((source, "media/" + filename))
        for source, destination in sorted(set(paths), key=lambda pair: pair[1]):
            blob = git("hash-object", "-w", str(source))
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{destination}", env=env)
        tree = git("write-tree", env=env)
        if expected and git("rev-parse", expected + "^{tree}") == tree:
            print("Feed unchanged; nothing to publish")
            return
        commit = git("commit-tree", tree, input=b"Update bounded wallpaper feed\n")
        # Expected SHA comes from the earlier fetch. Refuse to overwrite concurrent changes.
        git("push", f"--force-with-lease=refs/heads/wallpapers:{expected}", "origin", f"{commit}:refs/heads/wallpapers")
        print(f"Published {len(rows)} wallpapers on the single-commit data branch")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-file", type=Path, required=True)
    args = parser.parse_args()
    publish(args.repository, args.expected_file)
