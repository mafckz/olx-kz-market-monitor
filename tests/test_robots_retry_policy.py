from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from olx_monitor.monitor import _retry
from olx_monitor.settings import Settings
from olx_monitor.sources.olx import OLX_ORIGIN, ROBOTS_URL, OlxClient, OlxSourceError, RobotsUnavailable


class RobotsRetryPolicyTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, responses: list[object]) -> tuple[OlxClient, AsyncMock]:
        client = OlxClient(Settings(home=Path("."), request_delay_seconds=0))
        get = AsyncMock(side_effect=responses)
        client.client = SimpleNamespace(get=get)
        return client, get

    async def test_robots_3xx_and_4xx_stop_without_retry_or_category_request(self) -> None:
        category_url = f"{OLX_ORIGIN}/elektronika/kompyutery-i-komplektuyuschie/nastolnye-kompyutery/"

        for status_code in (302, 401, 403, 404, 429):
            with self.subTest(status_code=status_code):
                client, get = self.make_client([SimpleNamespace(status_code=status_code)])

                with self.assertRaises(RobotsUnavailable):
                    await _retry(lambda: client.get_html(category_url), attempts=3)

                get.assert_awaited_once_with(ROBOTS_URL)

    async def test_robots_5xx_uses_bounded_retry_limit(self) -> None:
        unavailable = [SimpleNamespace(status_code=503)] * 3
        client, get = self.make_client(unavailable)

        with patch("olx_monitor.monitor.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(OlxSourceError):
                await _retry(client._load_robots, attempts=3)

        self.assertEqual(get.await_count, 3)
        self.assertTrue(all(call.args == (ROBOTS_URL,) for call in get.await_args_list))

    async def test_robots_timeout_and_network_errors_can_retry(self) -> None:
        allowed = SimpleNamespace(status_code=200, text="User-agent: *\nAllow: /")

        for error in (httpx.TimeoutException("timed out"), httpx.ConnectError("network error")):
            with self.subTest(error=type(error).__name__):
                client, get = self.make_client([error, allowed])

                with patch("olx_monitor.monitor.asyncio.sleep", new_callable=AsyncMock):
                    await _retry(client._load_robots, attempts=3)

                self.assertEqual(get.await_count, 2)
                self.assertIsNotNone(client.robot_parser)


if __name__ == "__main__":
    unittest.main()
