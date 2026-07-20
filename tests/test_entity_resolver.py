import sqlite3
from unittest.mock import MagicMock, patch

from cpo_chain import db as usci_db
from cpo_chain.entity_resolver import EntityResolver


def _make_test_db():
    conn = sqlite3.connect(":memory:")
    usci_db.init_usci_tables(conn)
    return conn


def _make_resolver(tmp_path, wikidata_min_interval=0.0):
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text(
        "seed_aliases:\n"
        "  NVIDIA:\n"
        "    - NVDA\n"
        "root_tickers:\n"
        "  - NVDA\n",
        encoding="utf-8",
    )
    return EntityResolver(tmp_path / "tweets.db", keywords_path, wikidata_min_interval=wikidata_min_interval)


def _wikidata_response(entity_id="Q1", ticker="ACME"):
    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {"search": [{"id": entity_id}]}
    search_resp.headers = {}

    detail_resp = MagicMock()
    detail_resp.status_code = 200
    detail_resp.headers = {}
    claims = {}
    if ticker:
        claims["P249"] = [{"mainsnak": {"datavalue": {"value": ticker}}}]
    detail_resp.json.return_value = {"entities": {entity_id: {"claims": claims}}}
    return search_resp, detail_resp


# ---------------------------------------------------------------------------
# Pre-existing behavior (no coverage previously existed for this module)
# ---------------------------------------------------------------------------

def test_resolve_seed_alias_creates_entity_with_ticker(tmp_path):
    conn = _make_test_db()
    resolver = _make_resolver(tmp_path)

    company_id, name, status = resolver.resolve(conn, "NVDA")

    assert name == "NVIDIA"
    assert status == "active"
    row = conn.execute("SELECT ticker FROM industry_entities WHERE id = ?", (company_id,)).fetchone()
    assert row[0] == "NVDA"


def test_resolve_exact_db_alias_match_skips_wikidata(tmp_path):
    conn = _make_test_db()
    resolver = _make_resolver(tmp_path)
    cursor = conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('Lumentum', 'LITE')")
    company_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO industry_entity_aliases (alias, company_id, status) VALUES ('LITE', ?, 'active')",
        (company_id,),
    )
    conn.commit()

    with patch("cpo_chain.entity_resolver.requests.get") as mock_get:
        result_id, name, status = resolver.resolve(conn, "LITE")

    assert result_id == company_id
    assert name == "Lumentum"
    mock_get.assert_not_called()


def test_resolve_new_entity_falls_back_to_wikidata_and_stores_ticker(tmp_path):
    conn = _make_test_db()
    resolver = _make_resolver(tmp_path)
    search_resp, detail_resp = _wikidata_response(ticker="ACME")

    with patch("cpo_chain.entity_resolver.requests.get", side_effect=[search_resp, detail_resp]):
        company_id, name, status = resolver.resolve(conn, "Acme Corp")

    assert name == "Acme Corp"
    assert status == "needs_review"
    row = conn.execute("SELECT ticker FROM industry_entities WHERE id = ?", (company_id,)).fetchone()
    assert row[0] == "ACME"


# ---------------------------------------------------------------------------
# Wikidata result caching
# ---------------------------------------------------------------------------

def test_query_wikidata_caches_result_for_repeated_name(tmp_path):
    resolver = _make_resolver(tmp_path)
    search_resp, detail_resp = _wikidata_response(ticker="ACME")

    with patch("cpo_chain.entity_resolver.requests.get", side_effect=[search_resp, detail_resp]) as mock_get:
        first = resolver._query_wikidata("Acme Corp")
        second = resolver._query_wikidata("Acme Corp")

    assert first == {"ticker": "ACME"}
    assert second == {"ticker": "ACME"}
    assert mock_get.call_count == 2  # only the FIRST call actually hit the network


def test_query_wikidata_caches_negative_result_too(tmp_path):
    resolver = _make_resolver(tmp_path)
    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.headers = {}
    empty_resp.json.return_value = {"search": []}

    with patch("cpo_chain.entity_resolver.requests.get", return_value=empty_resp) as mock_get:
        first = resolver._query_wikidata("Nonexistent Co")
        second = resolver._query_wikidata("Nonexistent Co")

    assert first == {}
    assert second == {}
    assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

def test_throttle_wikidata_does_not_sleep_on_first_ever_call(tmp_path):
    resolver = _make_resolver(tmp_path, wikidata_min_interval=1.0)

    # _last_wikidata_call starts at 0.0; a monotonic clock far past that
    # (e.g. the process has been up a while) means no wait is needed yet.
    with patch("cpo_chain.entity_resolver.time.monotonic", return_value=10_000.0), \
         patch("cpo_chain.entity_resolver.time.sleep") as mock_sleep:
        resolver._throttle_wikidata()

    mock_sleep.assert_not_called()


def test_throttle_wikidata_waits_for_minimum_interval_on_next_call(tmp_path):
    resolver = _make_resolver(tmp_path, wikidata_min_interval=1.0)
    clock = {"t": 10_000.0}

    with patch("cpo_chain.entity_resolver.time.monotonic", side_effect=lambda: clock["t"]), \
         patch("cpo_chain.entity_resolver.time.sleep") as mock_sleep:
        resolver._throttle_wikidata()  # first call: establishes _last_wikidata_call
        clock["t"] += 0.1  # only 0.1s of real time passes before the next request
        resolver._throttle_wikidata()

    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args[0][0]
    assert 0.85 <= waited <= 0.95  # ~0.9s remaining to reach the 1.0s floor


def test_query_wikidata_throttles_between_the_two_http_calls_it_makes(tmp_path):
    # _fetch_wikidata makes two sequential requests (search + entity detail);
    # both must go through the same throttle so neither can bypass the floor.
    resolver = _make_resolver(tmp_path, wikidata_min_interval=1.0)
    search_resp, detail_resp = _wikidata_response(ticker="A")

    with patch("cpo_chain.entity_resolver.requests.get", side_effect=[search_resp, detail_resp]), \
         patch("cpo_chain.entity_resolver.time.monotonic", return_value=10_000.0), \
         patch("cpo_chain.entity_resolver.time.sleep") as mock_sleep:
        resolver._query_wikidata("Company A")

    # constant monotonic() means the 2nd internal request sees 0 elapsed time
    # since the 1st -> it must wait the full interval.
    mock_sleep.assert_called_once_with(1.0)


# ---------------------------------------------------------------------------
# Retry/backoff on HTTP 429
# ---------------------------------------------------------------------------

def test_query_wikidata_retries_on_429_then_succeeds(tmp_path):
    resolver = _make_resolver(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}
    search_resp, detail_resp = _wikidata_response(ticker="ACME")

    with patch("cpo_chain.entity_resolver.requests.get",
               side_effect=[rate_limited, search_resp, detail_resp]), \
         patch("cpo_chain.entity_resolver.time.sleep") as mock_sleep:
        result = resolver._query_wikidata("Acme Corp")

    assert result == {"ticker": "ACME"}
    assert mock_sleep.call_count >= 1  # backed off at least once before retrying


def test_query_wikidata_respects_retry_after_header(tmp_path):
    resolver = _make_resolver(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "5"}
    search_resp, detail_resp = _wikidata_response(ticker="ACME")

    with patch("cpo_chain.entity_resolver.requests.get",
               side_effect=[rate_limited, search_resp, detail_resp]), \
         patch("cpo_chain.entity_resolver.time.sleep") as mock_sleep:
        resolver._query_wikidata("Acme Corp")

    assert 5.0 in mock_sleep.call_args_list[0][0]


def test_query_wikidata_gives_up_after_max_retries_and_returns_empty(tmp_path):
    from cpo_chain.entity_resolver import _WIKIDATA_MAX_RETRIES

    resolver = _make_resolver(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {}
    # A real 429 response body isn't the shape _fetch_wikidata expects (no
    # "search" key); reflect that instead of relying on MagicMock auto-truthiness.
    rate_limited.json.return_value = {}

    with patch("cpo_chain.entity_resolver.requests.get", return_value=rate_limited) as mock_get, \
         patch("cpo_chain.entity_resolver.time.sleep"):
        result = resolver._query_wikidata("Acme Corp")

    assert result == {}
    # exactly _WIKIDATA_MAX_RETRIES + 1 attempts -- not an off-by-one, not unbounded
    assert mock_get.call_count == _WIKIDATA_MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# Exception handling in _fetch_wikidata (previously untested)
# ---------------------------------------------------------------------------

def test_fetch_wikidata_returns_empty_when_request_raises(tmp_path):
    resolver = _make_resolver(tmp_path)

    with patch("cpo_chain.entity_resolver.requests.get", side_effect=ConnectionError("boom")):
        result = resolver._query_wikidata("Acme Corp")

    assert result == {}


def test_fetch_wikidata_returns_empty_when_response_is_not_json(tmp_path):
    resolver = _make_resolver(tmp_path)
    bad_json_resp = MagicMock()
    bad_json_resp.status_code = 200
    bad_json_resp.headers = {}
    bad_json_resp.json.side_effect = ValueError("not json")

    with patch("cpo_chain.entity_resolver.requests.get", return_value=bad_json_resp):
        result = resolver._query_wikidata("Acme Corp")

    assert result == {}


def test_fetch_wikidata_returns_empty_when_ticker_claim_is_malformed(tmp_path):
    # A P249 claim missing the expected nested "datavalue"/"value" structure
    # must not raise KeyError/TypeError out of _fetch_wikidata.
    resolver = _make_resolver(tmp_path)
    entity_id = "Q1"
    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.headers = {}
    search_resp.json.return_value = {"search": [{"id": entity_id}]}

    detail_resp = MagicMock()
    detail_resp.status_code = 200
    detail_resp.headers = {}
    detail_resp.json.return_value = {
        "entities": {entity_id: {"claims": {"P249": [{"mainsnak": {}}]}}}  # no "datavalue"
    }

    with patch("cpo_chain.entity_resolver.requests.get", side_effect=[search_resp, detail_resp]):
        result = resolver._query_wikidata("Acme Corp")

    assert result == {}
