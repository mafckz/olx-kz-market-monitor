from __future__ import annotations

import asyncio
import pathlib
import urllib.robotparser

import httpx


ROBOTS = "https://www.olx.kz/robots.txt"
URL = (
    "https://www.olx.kz/elektronika/kompyutery-i-komplektuyuschie/"
    "komplektuyuschie-i-aksesuary/alma-ata/q-rtx-3080/"
)
USER_AGENT = "OLXMarketMonitor/0.1 (personal-use; respects robots.txt)"
OUTPUT = pathlib.Path(__file__).resolve().parents[1] / ".tmp" / "olx-gpu-probe.html"


async def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        robots = await client.get(ROBOTS)
        print(f"robots_status={robots.status_code}")
        if robots.status_code != 200:
            return 2

        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(ROBOTS)
        parser.parse(robots.text.splitlines())
        allowed = parser.can_fetch(USER_AGENT, URL)
        print(f"category_allowed={allowed}")
        if not allowed:
            return 3

        page = await client.get(URL)
        print(f"category_status={page.status_code}")
        print(f"content_type={page.headers.get('content-type', '')}")
        print(f"bytes={len(page.content)}")
        if page.status_code != 200:
            return 4

        OUTPUT.write_bytes(page.content)
        print(f"saved={OUTPUT}")
        return 0


raise SystemExit(asyncio.run(main()))
