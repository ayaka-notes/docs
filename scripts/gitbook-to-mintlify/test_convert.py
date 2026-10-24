"""Run the converter against small GitBook exports to check asset cleanup."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent


class AssetCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.src = root / "gitbook"
        self.dst = root / "mintlify"
        self.scripts = root / "scripts"
        self.scripts.mkdir()
        for name in ("convert.py", "icon-types.json"):
            shutil.copyfile(SCRIPT_DIR / name, self.scripts / name)
        for section in ("home", "on-premises", "blog", "texlive", "dev"):
            self.write(self.src / "docs" / section / "SUMMARY.md", "# Summary\n")
        self.write(self.src / "docs/home/README.md", '# Home\n\n![New](.gitbook/assets/new.png)\n')
        self.write(self.src / "docs/home/.gitbook/assets/new.png", "new image")
        self.write(self.dst / "docs.json", json.dumps({"navigation": {"languages": [
            {"language": "en", "default": True, "tabs": []},
            {"language": "zh-CN", "tabs": []},
        ]}}))

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def sync(self):
        result = subprocess.run(
            [sys.executable, str(self.scripts / "convert.py"), "--src", str(self.src),
             "--dst", str(self.dst), "--strict"], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_home_image_survives_until_translations_catch_up(self):
        old = self.dst / "images/home/image-6.png"
        self.write(old, "old image")
        self.write(self.dst / "index.mdx", '<img src="/images/home/image-6.png" />\n')
        locales = ("zh-CN", "zh-TW", "zh-Hans", "zh-Hant", "ja", "fr")
        for locale in locales:
            self.write(self.dst / locale / "index.mdx", '<img src="/images/home/image-6.png" />\n')
        self.sync()
        self.assertTrue(old.exists(), "Sync deleted an image still used by translations")
        self.assertIn("/images/home/new.png", (self.dst / "index.mdx").read_text())
        self.assertEqual(old.read_text(), "old image")
        self.sync()
        self.assertTrue(old.exists(), "A second sync deleted the translated image")
        for locale in locales:
            self.write(self.dst / locale / "index.mdx", '<img src="/images/home/new.png" />\n')
        self.sync()
        self.assertFalse(old.exists(), "Unreferenced images should still be cleaned up")

    def test_preserves_shared_media_and_download_references(self):
        paths = ("card.png", "demo.mp4", "download.pdf", "markdown.png", "cover.png", "encoded.png")
        for name in paths:
            self.write(self.dst / "images/blog" / name, name)
        self.write(self.dst / "fr/blog.mdx", '''---
title: Blog
image: /images/blog/cover.png
---
<Card img="/images/blog/card.png" />
<video src='/images/blog/demo.mp4?autoplay=1' />
<Card href="/images/blog/download.pdf#page=2" />
![Image](/images/blog/markdown.png)
[Image](/images/blog/%65ncoded.png)
''')
        self.sync()
        for name in paths:
            with self.subTest(name=name):
                self.assertTrue((self.dst / "images/blog" / name).exists())

    def test_removes_assets_used_only_by_stale_english_pages(self):
        self.write(self.dst / "blog/deleted.mdx", '![Old](/images/blog/deleted.png)\n')
        self.write(self.dst / "images/blog/deleted.png", "unused image")
        self.write(self.dst / "images/home/orphan.png", "orphan")
        self.sync()
        self.assertFalse((self.dst / "blog/deleted.mdx").exists())
        self.assertFalse((self.dst / "images/blog/deleted.png").exists())
        self.assertFalse((self.dst / "images/home/orphan.png").exists())

    def test_ignores_external_urls_and_non_site_files(self):
        self.write(self.dst / "zh-CN/index.mdx", '<img src="https://example.com/images/home/external.png" />\n')
        self.write(self.dst / "node_modules/pkg/README.md", '![Example](/images/home/dependency.png)\n')
        self.write(self.dst / "scripts/fixture.md", '![Fixture](/images/home/fixture.png)\n')
        for name in ("external.png", "dependency.png", "fixture.png"):
            self.write(self.dst / "images/home" / name, name)
        self.sync()
        for name in ("external.png", "dependency.png", "fixture.png"):
            with self.subTest(name=name):
                self.assertFalse((self.dst / "images/home" / name).exists())

    def test_preserves_derived_asset_sources_until_last_reference_is_removed(self):
        derived = {"images/home/demo.mp4": "sha1:old-video", "images/texlive/cdn.png": "url:cached/image.png"}
        self.write(self.scripts / "derived-assets.json", json.dumps(derived))
        for name in derived:
            self.write(self.dst / name, "existing bytes")
        page = self.dst / "ja/index.mdx"
        self.write(page, '<video src="/images/home/demo.mp4" />\n![CDN](/images/texlive/cdn.png)\n')
        self.sync()
        self.assertEqual(json.loads((self.scripts / "derived-assets.json").read_text()), derived)
        self.sync()
        self.assertEqual(json.loads((self.scripts / "derived-assets.json").read_text()), derived)
        page.unlink()
        self.sync()
        self.assertEqual(json.loads((self.scripts / "derived-assets.json").read_text()), {})
        for name in derived:
            self.assertFalse((self.dst / name).exists())


if __name__ == "__main__":
    unittest.main()
