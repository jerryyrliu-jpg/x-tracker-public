
import pytest
from cpo_chain.edgar_fetcher import EdgarFetcher

class MockResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
    def json(self):
        return self.json_data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")

def test_edgar_found_10k(monkeypatch):
    fetcher = EdgarFetcher()
    mock_data = {
        "hits": {
            "hits": [
                {"_source": {"form_type": "10-K", "file_date": "2024-03-01"}}
            ]
        }
    }
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockResponse(mock_data))
    
    hits = fetcher.search_relation("NVIDIA", "TSMC")
    assert len(hits) == 1
    assert hits[0]["form_type"] == "10-K"
    assert fetcher.calc_edgar_score(hits) == 0.20

def test_edgar_found_multiple(monkeypatch):
    fetcher = EdgarFetcher()
    mock_data = {
        "hits": {
            "hits": [
                {"_source": {"form_type": "8-K"}},
                {"_source": {"form_type": "8-K"}},
                {"_source": {"form_type": "8-K"}}
            ]
        }
    }
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockResponse(mock_data))
    
    hits = fetcher.search_relation("A", "B")
    assert len(hits) == 3
    assert fetcher.calc_edgar_score(hits) == 0.30

def test_edgar_no_result(monkeypatch):
    fetcher = EdgarFetcher()
    mock_data = {"hits": {"hits": []}}
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockResponse(mock_data))
    
    hits = fetcher.search_relation("X", "Y")
    assert len(hits) == 0
    assert fetcher.calc_edgar_score(hits) == 0.0

def test_edgar_429_retry(monkeypatch):
    fetcher = EdgarFetcher()
    call_count = 0
    
    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResponse({}, 429)
        return MockResponse({"hits": {"hits": []}}, 200)
        
    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("time.sleep", lambda x: None) # skip sleep
    
    hits = fetcher.search_relation("A", "B")
    assert call_count == 2
    assert len(hits) == 0
