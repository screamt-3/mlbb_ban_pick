#!/usr/bin/env python3
"""Build the versioned hero portrait catalog from the official MPL MY hero table."""

from __future__ import annotations

import argparse
import html as html_module
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml


SOURCE_URL = "https://my.mpl.mobilelegends.com/en/"
ENTRY = re.compile(
    r'<td class="col-hero" data-order="(?P<name>[^"]+)">.*?'
    r'<img src="(?P<url>https://wsrv\.nl/?\?url=[^"]+)"\s+'
    r'alt="[^"]+"',
    re.DOTALL,
)


def slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mlbb-draft-extractor/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=Path, help="Use a previously downloaded official page")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    html = (
        args.html.read_text(encoding="utf-8")
        if args.html
        else fetch(SOURCE_URL).decode("utf-8", errors="replace")
    )
    records: dict[str, tuple[str, str]] = {}
    for match in ENTRY.finditer(html):
        name = html_module.unescape(match.group("name").strip())
        proxy_url = match.group("url")
        inner_url = parse_qs(urlparse(proxy_url).query).get("url", [proxy_url])[0]
        records[slug(name)] = (name, proxy_url, inner_url)
    if len(records) < 100:
        raise SystemExit(f"only found {len(records)} hero portraits; refusing partial catalog")

    asset_root = project_root / "assets" / "heroes" / "msc-ewc-2026-v1"
    asset_root.mkdir(parents=True, exist_ok=True)
    heroes = []
    skipped: list[str] = []
    for hero_id, (name, download_url, source_asset_url) in sorted(records.items()):
        suffix = Path(urlparse(source_asset_url).path).suffix.lower() or ".jpg"
        asset_path = asset_root / f"{hero_id}{suffix}"
        if not asset_path.exists():
            try:
                asset_path.write_bytes(fetch(download_url))
            except urllib.error.HTTPError as exc:
                skipped.append(f"{name} ({exc.code})")
                continue
        relative = asset_path.relative_to(project_root).as_posix()
        heroes.append(
            {
                "hero_id": hero_id,
                "display_name": name,
                "references": [{"asset_id": f"official-mpl-my-{hero_id}", "path": relative}],
            }
        )

    catalog = {
        "schema_version": "1.0",
        "catalog_id": "msc-ewc-2026-heroes-v1",
        "version": "2026-09-11",
        "patch": "msc-ewc-2026-competitive-patch-not-publicly-disclosed",
        "source": SOURCE_URL,
        "heroes": heroes,
    }
    destination = project_root / "configs" / "catalogs" / "msc-ewc-2026-heroes-v1.yaml"
    destination.write_text(
        yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    if len(heroes) < 100:
        raise SystemExit(f"only downloaded {len(heroes)} usable portraits; refusing partial catalog")
    print(f"wrote {len(heroes)} heroes to {destination}")
    if skipped:
        print("skipped unavailable official assets: " + ", ".join(skipped))


if __name__ == "__main__":
    main()
