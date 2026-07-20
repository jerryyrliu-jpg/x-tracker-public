"""Unit tests for scraper_playwright.py."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scraper_playwright


class _DummyPlaywrightContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _dummy_playwright_factory():
    return _DummyPlaywrightContext()


class TestCdpRestartHeuristic:
    def test_detects_econnrefused(self):
        assert scraper_playwright._is_cdp_connection_error(
            RuntimeError("connect ECONNREFUSED 127.0.0.1:9222")
        )

    def test_detects_websocket_url_retrieval_failure(self):
        assert scraper_playwright._is_cdp_connection_error(
            RuntimeError("retrieving websocket url from http://127.0.0.1:9222")
        )

    def test_ignores_page_level_errors(self):
        assert not scraper_playwright._is_cdp_connection_error(
            RuntimeError("Page.goto: Timeout 30000ms exceeded")
        )


class TestPlaywrightRetryHeuristic:
    def test_target_closed_is_retryable(self):
        assert scraper_playwright._is_retryable_playwright_error(
            RuntimeError("TargetClosedError: Target page, context or browser has been closed")
        )

    def test_execution_context_destroyed_is_retryable(self):
        assert scraper_playwright._is_retryable_playwright_error(
            RuntimeError("Execution context was destroyed, most likely because of a navigation")
        )

    def test_generic_timeout_is_not_retryable(self):
        assert not scraper_playwright._is_retryable_playwright_error(
            RuntimeError("article selector not found")
        )


async def test_intercept_route_allows_image_requests_for_tweet_media():
    route = type("Route", (), {})()
    route.request = type("Request", (), {
        "url": "https://pbs.twimg.com/media/HMd5VmYbEAAEtp4?format=jpg&name=small",
        "resource_type": "image",
    })()
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()

    await scraper_playwright.intercept_route(route)

    route.continue_.assert_awaited_once()
    route.abort.assert_not_called()


async def test_intercept_route_still_blocks_video_and_font_requests():
    video_route = type("Route", (), {})()
    video_route.request = type("Request", (), {
        "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4",
        "resource_type": "media",
    })()
    video_route.abort = AsyncMock()
    video_route.continue_ = AsyncMock()

    font_route = type("Route", (), {})()
    font_route.request = type("Request", (), {
        "url": "https://abs.twimg.com/responsive-web/client-web/font.woff2",
        "resource_type": "font",
    })()
    font_route.abort = AsyncMock()
    font_route.continue_ = AsyncMock()

    await scraper_playwright.intercept_route(video_route)
    await scraper_playwright.intercept_route(font_route)

    video_route.abort.assert_awaited_once()
    video_route.continue_.assert_not_called()
    font_route.abort.assert_awaited_once()
    font_route.continue_.assert_not_called()


def test_build_tweet_image_dir_uses_account_and_tweet_id():
    path = scraper_playwright._build_tweet_image_dir("test_account_1", "123")
    assert str(path).endswith("images/test_account_1/123")


def test_filter_tweet_image_urls_keeps_https_photos_only():
    urls = [
        "https://pbs.twimg.com/media/a.jpg",
        "http://pbs.twimg.com/media/b.jpg",
        "https://video.twimg.com/ext_tw_video/1/pu/vid.mp4",
        "https://pbs.twimg.com/profile_images/x.png",
    ]
    kept = scraper_playwright._filter_tweet_image_urls(urls)
    assert kept == ["https://pbs.twimg.com/media/a.jpg"]


async def test_extract_tweet_image_urls_reads_img_srcs():
    class FakeImg:
        def __init__(self, src):
            self.src = src

        async def get_attribute(self, name):
            assert name == "src"
            return self.src

    class FakeTweet:
        async def query_selector_all(self, selector):
            assert selector == "img"
            return [
                FakeImg("https://pbs.twimg.com/media/a.jpg"),
                FakeImg("https://pbs.twimg.com/profile_images/avatar.png"),
                FakeImg(None),
            ]

    urls = await scraper_playwright._extract_tweet_image_urls(FakeTweet())
    assert urls == ["https://pbs.twimg.com/media/a.jpg"]


async def test_download_tweet_images_saves_files(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper_playwright, "IMAGES_ROOT", tmp_path)

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse(content=f"body:{url}".encode())

    monkeypatch.setattr(scraper_playwright.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    paths = await scraper_playwright._download_tweet_images(
        account="test_account_1",
        tweet_id="123",
        urls=["https://pbs.twimg.com/media/a.jpg?format=jpg&name=small"],
    )

    assert len(paths) == 1
    assert Path(paths[0]).exists()


async def test_download_tweet_images_returns_empty_on_http_error(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper_playwright, "IMAGES_ROOT", tmp_path)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            raise RuntimeError("boom")

    monkeypatch.setattr(scraper_playwright.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    paths = await scraper_playwright._download_tweet_images(
        account="test_account_1",
        tweet_id="123",
        urls=["https://pbs.twimg.com/media/a.jpg"],
    )

    assert paths == []


def test_select_tweets_missing_images_returns_only_empty_image_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tweets (id TEXT PRIMARY KEY, account TEXT, created_at TEXT, text TEXT, images TEXT, scraped_at TEXT)"
    )
    conn.execute(
        "INSERT INTO tweets (id, account, created_at, text, images, scraped_at) VALUES "
        "('1', 'test_account_1', '2026-07-05T10:00:00Z', 'a', '[]', '2026-07-05T10:00:00Z'),"
        "('2', 'test_account_1', '2026-07-05T11:00:00Z', 'b', '[\"/tmp/1.jpg\"]', '2026-07-05T11:00:00Z'),"
        "('3', 'test_account_2', '2026-07-05T12:00:00Z', 'c', '[]', '2026-07-05T12:00:00Z')"
    )
    rows = scraper_playwright._select_tweets_missing_images(conn, account="test_account_1", limit=10)
    assert [(row["id"], row["account"]) for row in rows] == [("1", "test_account_1")]


def test_update_tweet_images_writes_json_paths():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE tweets (id TEXT PRIMARY KEY, account TEXT, created_at TEXT, text TEXT, images TEXT, scraped_at TEXT)"
    )
    conn.execute(
        "INSERT INTO tweets (id, account, created_at, text, images, scraped_at) VALUES "
        "('1', 'test_account_1', '2026-07-05T10:00:00Z', 'a', '[]', '2026-07-05T10:00:00Z')"
    )
    scraper_playwright._update_tweet_images(conn, "1", ["/tmp/1.jpg"])
    row = conn.execute("SELECT images FROM tweets WHERE id='1'").fetchone()
    assert json.loads(row[0]) == ["/tmp/1.jpg"]


async def test_scrape_once_inserts_saved_image_paths(monkeypatch):
    captured = {}

    async def fake_download(account, tweet_id, urls):
        assert account == scraper_playwright.ACCOUNT
        assert tweet_id == "123"
        assert urls == ["https://pbs.twimg.com/media/a.jpg"]
        return ["/tmp/images/123/1.jpg"]

    class FakeLink:
        async def get_attribute(self, name):
            return "/user/status/123"

    class FakeText:
        async def inner_text(self):
            return "tweet body"

    class FakeTime:
        async def get_attribute(self, name):
            return "2026-07-05T12:00:00Z"

    class FakeTweet:
        async def query_selector(self, selector):
            mapping = {
                "a[href*='/status/']": FakeLink(),
                "[data-testid='tweetText']": FakeText(),
                "time": FakeTime(),
            }
            return mapping.get(selector)

        async def query_selector_all(self, selector):
            if selector == "img":
                class FakeImg:
                    async def get_attribute(self, name):
                        return "https://pbs.twimg.com/media/a.jpg"

                return [FakeImg()]
            return []

    class FakeCursor:
        rowcount = 1

        def execute(self, sql, params):
            captured["params"] = params

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def close(self):
            return None

    class FakePage:
        async def route(self, pattern, handler):
            return None

        async def goto(self, url, wait_until="load", timeout=60000):
            return None

        async def wait_for_selector(self, selector, timeout=30000):
            return None

        async def query_selector_all(self, selector):
            assert selector == "article[data-testid='tweet']"
            return [FakeTweet()]

        async def close(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            return None

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        async def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    async def fake_extract(tweet):
        return ["https://pbs.twimg.com/media/a.jpg"]

    async def fake_scroll(page):
        return None

    monkeypatch.setattr(scraper_playwright, "_download_tweet_images", fake_download)
    monkeypatch.setattr(scraper_playwright, "_extract_tweet_image_urls", fake_extract)
    monkeypatch.setattr(scraper_playwright, "get_db_conn", lambda path: FakeConn())
    monkeypatch.setattr(scraper_playwright, "_ensure_fts_triggers", lambda conn: None)
    monkeypatch.setattr(scraper_playwright, "human_like_scroll", fake_scroll)
    monkeypatch.setattr(scraper_playwright.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(scraper_playwright, "post_to_discord", fake_scroll)

    result = await scraper_playwright._scrape_once(FakePlaywright())

    assert result["status"] == "success"
    assert json.loads(captured["params"][4]) == ["/tmp/images/123/1.jpg"]


async def test_scrape_once_keeps_ingesting_tweet_when_image_download_fails(monkeypatch):
    captured = {}

    async def fake_download(account, tweet_id, urls):
        raise RuntimeError("boom")

    class FakeLink:
        async def get_attribute(self, name):
            return "/user/status/123"

    class FakeText:
        async def inner_text(self):
            return "tweet body"

    class FakeTime:
        async def get_attribute(self, name):
            return "2026-07-05T12:00:00Z"

    class FakeTweet:
        async def query_selector(self, selector):
            mapping = {
                "a[href*='/status/']": FakeLink(),
                "[data-testid='tweetText']": FakeText(),
                "time": FakeTime(),
            }
            return mapping.get(selector)

        async def query_selector_all(self, selector):
            if selector == "img":
                class FakeImg:
                    async def get_attribute(self, name):
                        return "https://pbs.twimg.com/media/a.jpg"

                return [FakeImg()]
            return []

    class FakeCursor:
        rowcount = 1

        def execute(self, sql, params):
            captured["params"] = params

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def close(self):
            return None

    class FakePage:
        async def route(self, pattern, handler):
            return None

        async def goto(self, url, wait_until="load", timeout=60000):
            return None

        async def wait_for_selector(self, selector, timeout=30000):
            return None

        async def query_selector_all(self, selector):
            assert selector == "article[data-testid='tweet']"
            return [FakeTweet()]

        async def close(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            return None

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        async def connect_over_cdp(self, url):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    async def fake_extract(tweet):
        return ["https://pbs.twimg.com/media/a.jpg"]

    async def fake_scroll(page):
        return None

    monkeypatch.setattr(scraper_playwright, "_download_tweet_images", fake_download)
    monkeypatch.setattr(scraper_playwright, "_extract_tweet_image_urls", fake_extract)
    monkeypatch.setattr(scraper_playwright, "get_db_conn", lambda path: FakeConn())
    monkeypatch.setattr(scraper_playwright, "_ensure_fts_triggers", lambda conn: None)
    monkeypatch.setattr(scraper_playwright, "human_like_scroll", fake_scroll)
    monkeypatch.setattr(scraper_playwright.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(scraper_playwright, "post_to_discord", fake_scroll)

    result = await scraper_playwright._scrape_once(FakePlaywright())

    assert result["status"] == "success"
    assert json.loads(captured["params"][4]) == []


async def test_run_scrape_with_retries_restarts_cdp_once(monkeypatch):
    attempts = []
    restarts = []

    async def fake_scrape_once(_playwright):
        attempts.append("attempt")
        if len(attempts) == 1:
            raise RuntimeError("connect ECONNREFUSED 127.0.0.1:9222")
        return {"status": "success", "new_count": 2, "message": ""}

    async def fake_restart():
        restarts.append("restart")
        return True

    monkeypatch.setattr(scraper_playwright, "_scrape_once", fake_scrape_once)
    monkeypatch.setattr(scraper_playwright, "_restart_cdp_chrome", fake_restart)

    result = await scraper_playwright._run_scrape_with_retries(
        playwright_factory=_dummy_playwright_factory
    )

    assert result["status"] == "success"
    assert result["new_count"] == 2
    assert len(attempts) == 2
    assert restarts == ["restart"]


async def test_run_scrape_with_retries_retries_target_closed(monkeypatch):
    attempts = []
    sleeps = []

    async def fake_scrape_once(_playwright):
        attempts.append("attempt")
        if len(attempts) == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        return {"status": "success", "new_count": 1, "message": ""}

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(scraper_playwright, "_scrape_once", fake_scrape_once)

    result = await scraper_playwright._run_scrape_with_retries(
        playwright_factory=_dummy_playwright_factory,
        sleep_fn=fake_sleep,
    )

    assert result["status"] == "success"
    assert result["new_count"] == 1
    assert len(attempts) == 2
    assert sleeps == [2]


async def test_run_scrape_with_retries_returns_error_when_restart_fails(monkeypatch):
    async def fake_scrape_once(_playwright):
        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:9222")

    async def fake_restart():
        return False

    monkeypatch.setattr(scraper_playwright, "_scrape_once", fake_scrape_once)
    monkeypatch.setattr(scraper_playwright, "_restart_cdp_chrome", fake_restart)

    result = await scraper_playwright._run_scrape_with_retries(
        playwright_factory=_dummy_playwright_factory
    )

    assert result["status"] == "error"
    assert "ECONNREFUSED" in result["message"]
