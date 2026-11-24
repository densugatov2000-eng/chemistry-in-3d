#!/usr/bin/env python3
"""Validate the trainer manifests and build the static Pages site."""

import json
import math
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STATUSES = {"published", "draft", "planned"}
EXAMS = {"ОГЭ", "ЕГЭ"}
TEXT_FIELDS = ("id", "title", "description", "category", "imageAlt")
LIST_FIELDS = ("exams", "features", "keywords")
MANIFEST_FIELDS = {*TEXT_FIELDS, *LIST_FIELDS, "number", "metrics", "status"}
REQUIRED_TRAINER_FILES = ("index.html", "preview.webp", "social-preview.png")
CATALOG_FILES = (
    "index.html", "styles.css", "catalog.js", "social.json", "favicon.svg",
    ".nojekyll", "assets/branding/logo.svg", "assets/branding/social-preview.png",
)
RESOURCE_FOLDERS = {"assets", "js", "css", "data"}
PUBLIC_EXTENSIONS = {
    ".html", ".css", ".js", ".mjs", ".json", ".csv", ".txt", ".wasm",
    ".svg", ".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".ogg", ".wav", ".mp4", ".webm",
    ".glb", ".gltf", ".bin", ".ktx2",
}
PRIVATE_FOLDERS = {"tests", "test", "scripts", "docs", "backups", "reports", "drafts"}
PRIVATE_NAMES = {"readme", "manifest", "backup", "report", "qa_report"}
MAX_SAFE_INTEGER = 2**53 - 1


class BuildError(ValueError):
    """A validation failure that can be fixed in the repository."""


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def require_file(path, *, allow_empty=False):
    if is_link(path) or not path.is_file():
        raise BuildError(f"{path}: требуется обычный файл")
    if not allow_empty and path.stat().st_size == 0:
        raise BuildError(f"{path}: файл пуст")


def require_dir(path):
    if is_link(path) or not path.is_dir():
        raise BuildError(f"{path}: требуется обычная папка")


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"повторяющееся поле {key!r}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"недопустимое число {value}")


def is_text(value):
    return (
        isinstance(value, str) and bool(value.strip())
        and all(not 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def finite_number(value):
    return (
        type(value) is int and abs(value) <= MAX_SAFE_INTEGER
        or type(value) is float and math.isfinite(value)
    )


def validate_manifest(path):
    require_file(path)
    try:
        data = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeError) as error:
        raise BuildError(f"{path}: некорректный JSON ({error})") from error

    if not isinstance(data, dict):
        raise BuildError(f"{path}: паспорт должен быть JSON-объектом")
    missing = sorted(MANIFEST_FIELDS - data.keys())
    unexpected = sorted(data.keys() - MANIFEST_FIELDS)
    if missing or unexpected:
        raise BuildError(f"{path}: отсутствуют поля {missing}, лишние поля {unexpected}")

    for key in TEXT_FIELDS:
        if not is_text(data[key]):
            raise BuildError(f"{path}: {key} должен быть непустой строкой")
    if not SLUG.fullmatch(data["id"]):
        raise BuildError(f"{path}: недопустимый id {data['id']!r}")
    if type(data["number"]) is not int or not 1 <= data["number"] <= MAX_SAFE_INTEGER:
        raise BuildError(f"{path}: number должен быть положительным целым числом")
    if not is_text(data["status"]) or data["status"] not in STATUSES:
        raise BuildError(f"{path}: недопустимый статус {data['status']!r}")

    for key in LIST_FIELDS:
        if not isinstance(data[key], list) or not all(map(is_text, data[key])):
            raise BuildError(f"{path}: {key} должен быть массивом непустых строк")
    if not data["exams"] or not set(data["exams"]) <= EXAMS or len(set(data["exams"])) != len(data["exams"]):
        raise BuildError(f"{path}: exams должен содержать ОГЭ и/или ЕГЭ без дублей")
    if not isinstance(data["metrics"], list):
        raise BuildError(f"{path}: metrics должен быть массивом")
    for item in data["metrics"]:
        if (not isinstance(item, dict) or set(item) != {"value", "label"}
                or not finite_number(item["value"]) or not is_text(item["label"])):
            raise BuildError(f"{path}: каждый показатель должен содержать число value и строку label")
    return data


def discover_trainers(root):
    simulator = root / "simulator"
    require_dir(simulator)
    known = {"id": {}, "number": {}}
    published = []

    for folder in sorted(simulator.iterdir()):
        if is_link(folder):
            raise BuildError(f"{folder}: ссылки в simulator запрещены")
        if not folder.is_dir():
            continue
        slug = folder.name
        if not SLUG.fullmatch(slug):
            raise BuildError(f"{folder}: недопустимое имя папки тренажёра")

        path = folder / "manifest.json"
        data = validate_manifest(path)
        for key, seen in known.items():
            value = data[key]
            if value in seen:
                raise BuildError(f"{path}: повторяющийся {key} {value!r}, также в {seen[value]}")
            seen[value] = path
        for name in REQUIRED_TRAINER_FILES:
            require_file(folder / name)

        if data["status"] == "published":
            url = f"./simulator/{slug}/"
            entry = dict(data, slug=slug, url=url, image=f"{url}preview.webp")
            published.append((folder, entry))

    return sorted(published, key=lambda item: item[1]["number"])


def trainer_resources(folder):
    for path in sorted(folder.iterdir()):
        if is_link(path):
            raise BuildError(f"{path}: ссылки в ресурсах тренажёра запрещены")
        if path.name.startswith("."):
            continue
        if path.is_dir():
            if path.name.lower() not in PRIVATE_FOLDERS:
                yield from trainer_resources(path)
        elif (path.suffix.lower() in PUBLIC_EXTENSIONS
              and path.stem.lower() not in PRIVATE_NAMES):
            require_file(path)
            yield path


def copy_trainer(folder, target):
    target.mkdir(parents=True)
    for name in REQUIRED_TRAINER_FILES:
        shutil.copyfile(folder / name, target / name)
    for path in sorted(folder.iterdir()):
        if is_link(path):
            raise BuildError(f"{path}: ссылки в тренажёре запрещены")
        if path.name in RESOURCE_FOLDERS:
            require_dir(path)
            for resource in trainer_resources(path):
                destination = target / resource.relative_to(folder)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(resource, destination)


def render_index(source, show_atomic):
    for name in ("atomic-preview", "atomic-link"):
        start = f"<!-- {name}:start -->".encode()
        end = f"<!-- {name}:end -->".encode()
        if source.count(start) != 1 or source.count(end) != 1 or source.index(start) > source.index(end):
            raise BuildError(f"index.html: неверные границы блока {name}")
        before, body = source.split(start, 1)
        block, after = body.split(end, 1)
        source = before + (block if show_atomic else b"") + after
    return source


def populate(root, stage, published):
    for folder, entry in published:
        copy_trainer(folder, stage / "simulator" / entry["slug"])
    for folder in ("assets", "assets/branding"):
        require_dir(root / folder)
    for name in CATALOG_FILES:
        require_file(root / name, allow_empty=name == ".nojekyll")
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)

    index = stage / "index.html"
    show_atomic = any(entry["slug"] == "atomic-structure" for _, entry in published)
    index.write_bytes(render_index(index.read_bytes(), show_atomic))

    registry = [entry for _, entry in published]
    data = json.dumps(registry, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
    (stage / "trainers.json").write_bytes((data + "\n").encode("utf-8"))


def build_catalog(root=ROOT):
    root = Path(root)
    published = discover_trainers(root)
    output = root / "dist"
    if output.exists() or is_link(output):
        require_dir(output)

    with TemporaryDirectory(prefix=".catalog-build-", dir=root) as temp:
        stage = Path(temp) / "site"
        previous = Path(temp) / "previous"
        stage.mkdir()
        populate(root, stage, published)
        if output.exists():
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    return len(published)


def main():
    try:
        count = build_catalog()
    except (BuildError, OSError) as error:
        print(f"Ошибка сборки: {error}", file=sys.stderr)
        return 1
    print(f"Сайт собран в dist/. Опубликовано тренажёров: {count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
