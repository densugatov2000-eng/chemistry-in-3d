import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin

from scripts.build_catalog import BuildError, ROOT, build_catalog

ATOMIC = ROOT / "simulator" / "atomic-structure"
EXPECTED_JS = "81c679441ba5a40de5df1c0d8adb6e300f73451949f50b87cc90e88bc62798a5"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("index.html", "styles.css", "catalog.js", "social.json", "favicon.svg", ".nojekyll"):
            shutil.copyfile(ROOT / name, self.root / name)
        shutil.copytree(ROOT / "assets", self.root / "assets")
        self.simulator = self.root / "simulator"
        self.simulator.mkdir()
        self.published = self.root / "dist"
        self.source_manifest = {
            "id": "fixture", "number": 1, "title": "Модель", "description": "Описание модели",
            "category": "Химия", "exams": ["ОГЭ"], "features": [], "metrics": [],
            "keywords": [], "imageAlt": "Вид модели", "status": "published",
        }

    def add_trainer(self, number, slug=None, status="published", **fields):
        slug = slug or f"trainer-{number}"
        folder = self.simulator / slug
        folder.mkdir()
        for name in ("index.html", "preview.webp", "social-preview.png"):
            (folder / name).write_bytes(b"test resource")
        manifest = dict(self.source_manifest, id=slug, number=number, status=status, **fields)
        self.write_manifest(folder, manifest)
        return folder

    def write_manifest(self, folder, manifest):
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def registry(self):
        return json.loads((self.published / "trainers.json").read_text(encoding="utf-8"))

    def test_scale_and_deterministic_results(self):
        for size in (1, 2, 3, 10, 30, 100):
            with self.subTest(size=size):
                for number in range(size, 0, -1):
                    if not (self.simulator / f"trainer-{number}").exists():
                        self.add_trainer(number)
                self.assertEqual(build_catalog(self.root), size)
                entries = self.registry()
                self.assertEqual([entry["number"] for entry in entries], list(range(1, size + 1)))
                for entry in entries:
                    path = f"./simulator/{entry['slug']}/"
                    self.assertEqual(entry["url"], path)
                    self.assertEqual(entry["image"], path + "preview.webp")
                    self.assertTrue((self.published / entry["image"]).is_file())
                registry = self.published / "trainers.json"
                before = registry.read_bytes()
                build_catalog(self.root)
                self.assertEqual(registry.read_bytes(), before)

    def test_changes_need_only_a_trainer_folder(self):
        original_sources = [self.root / name for name in ("index.html", "catalog.js", "styles.css")]
        before = [path.read_bytes() for path in original_sources]
        old = self.add_trainer(1, "atomic-structure")
        build_catalog(self.root)
        nested = self.add_trainer(2, "second-trainer", description="Новое описание")
        self.assertEqual(build_catalog(self.root), 2)
        self.assertEqual(self.registry()[1]["description"], "Новое описание")
        manifest = json.loads((nested / "manifest.json").read_text(encoding="utf-8"))
        manifest["description"] = "Уточнённое описание"
        self.write_manifest(nested, manifest)
        next_preview = b"replaced preview"
        (nested / "preview.webp").write_bytes(next_preview)
        build_catalog(self.root)
        self.assertEqual(self.registry()[1]["description"], "Уточнённое описание")
        self.assertEqual((self.published / self.registry()[1]["image"]).read_bytes(), next_preview)
        manifest["status"] = "draft"
        self.write_manifest(nested, manifest)
        build_catalog(self.root)
        self.assertFalse((self.published / "simulator" / nested.name).exists())
        shutil.rmtree(old)
        self.assertEqual(build_catalog(self.root), 0)
        self.assertEqual(self.registry(), [])
        self.assertNotIn("./simulator/atomic-structure/", (self.published / "index.html").read_text(encoding="utf-8"))
        self.assertEqual([path.read_bytes() for path in original_sources], before)

    def test_duplicate_ids_and_numbers(self):
        first = self.add_trainer(1)
        second = self.add_trainer(2)
        manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
        for key, value in (("id", first.name), ("number", 1)):
            with self.subTest(key=key):
                bad = dict(manifest)
                bad[key] = value
                self.write_manifest(second, bad)
                with self.assertRaisesRegex(BuildError, f"повторяющийся {key}"):
                    build_catalog(self.root)

    def test_missing_required_files(self):
        folder = self.add_trainer(1)
        for name in ("manifest.json", "index.html", "preview.webp", "social-preview.png"):
            with self.subTest(name=name):
                path = folder / name
                content = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(BuildError, re.escape(name)):
                    build_catalog(self.root)
                path.write_bytes(content)

    def test_invalid_manifest_json_and_status(self):
        folder = self.add_trainer(1)
        manifest = folder / "manifest.json"
        manifest.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "некорректный JSON"):
            build_catalog(self.root)
        for status in ("released", [], None):
            with self.subTest(status=status):
                self.write_manifest(folder, dict(self.source_manifest, status=status))
                with self.assertRaisesRegex(BuildError, "недопустимый статус"):
                    build_catalog(self.root)

    def test_directory_names_and_paths(self):
        folder = self.add_trainer(1, "Bad Name")
        with self.assertRaisesRegex(BuildError, "недопустимое имя"):
            build_catalog(self.root)
        folder.rename(self.simulator / "good-name")
        folder = self.simulator / "good-name"
        for field, value in (("id", "../another"), ("image", "../bad.webp"), ("url", "https://example.org")):
            with self.subTest(field=field):
                self.write_manifest(folder, dict(self.source_manifest, **{field: value}))
                with self.assertRaises(BuildError):
                    build_catalog(self.root)

    def test_symlink_cannot_escape_the_trainer(self):
        folder = self.add_trainer(1)
        path = folder / "preview.webp"
        path.unlink()
        try:
            path.symlink_to(self.root / "favicon.svg")
        except (NotImplementedError, OSError):
            self.skipTest("Создание символических ссылок недоступно")
        with self.assertRaisesRegex(BuildError, "preview.webp"):
            build_catalog(self.root)

    def test_drafts_and_private_files_are_not_published(self):
        published = self.add_trainer(1, "atomic-structure")
        self.add_trainer(2, status="draft")
        self.add_trainer(3, status="planned")
        (self.root / "tests").mkdir()
        (self.root / "tests/fixture.json").write_text("{}", encoding="utf-8")
        (published / "data" / "tests").mkdir(parents=True)
        (published / "data" / "tests" / "fixture.json").write_text("{}", encoding="utf-8")
        (published / "data" / "elements.json").write_text("{}", encoding="utf-8")
        (published / "data" / ".private.json").write_text("{}", encoding="utf-8")
        (published / "data" / "dump.zip").write_text("archive", encoding="utf-8")
        self.assertEqual(build_catalog(self.root), 1)
        target = self.published / "simulator" / "atomic-structure"
        self.assertEqual({path.name for path in target.iterdir()},
                         {"index.html", "preview.webp", "social-preview.png", "data"})
        self.assertEqual([path.name for path in (target / "data").iterdir()], ["elements.json"])
        self.assertEqual([path.name for path in (self.published / "simulator").iterdir()], ["atomic-structure"])
        self.assertFalse((self.published / "tests").exists())

    def test_failed_cli_build_keeps_previous_dist(self):
        folder = self.add_trainer(1)
        build_catalog(self.root)
        before = (self.published / "trainers.json").read_bytes()
        (folder / "manifest.json").write_text("{", encoding="utf-8")
        script = self.root / "scripts" / "build_catalog.py"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "scripts" / "build_catalog.py", script)
        result = subprocess.run([sys.executable, str(script)], capture_output=True, cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"manifest.json", result.stderr)
        self.assertEqual((self.published / "trainers.json").read_bytes(), before)

    def test_atomic_structure_paths_and_protected_script(self):
        folder = self.add_trainer(1, "atomic-structure")
        if not ATOMIC.is_dir():
            self.skipTest("Первый тренажёр отсутствует в исходниках")
        shutil.copyfile(ATOMIC / "index.html", folder / "index.html")
        build_catalog(self.root)
        entry = self.registry()[0]
        platform = "https://densugatov2000-eng.github.io/chemistry-in-3d/"
        url = urljoin(platform, entry["url"])
        html = (self.published / entry["url"] / "index.html").read_bytes()
        scripts = re.findall(rb"<script\b[^>]*>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(hashlib.sha256(scripts[0]).hexdigest(), EXPECTED_JS)
        self.assertIn(b"atomic-structure-trainer-v3", scripts[0])
        self.assertIn(b'href="../../"', html)
        self.assertEqual(urljoin(url, "../../"), platform)
        self.assertIn(b'href="../../favicon.svg"', html)
        self.assertIn(f'rel="canonical" href="{url}"'.encode(), html)
        self.assertIn(f'{url}social-preview.png'.encode(), html)
        index = (self.published / "index.html").read_bytes()
        self.assertIn(f'src="{entry["image"]}"'.encode(), index)
        self.assertIn(f'href="{entry["url"]}"'.encode(), index)
        self.assertNotIn(b"atomic-structure-model", index + html)
        self.assertFalse((self.published / "assets" / "trainers").exists())


if __name__ == "__main__":
    unittest.main()
