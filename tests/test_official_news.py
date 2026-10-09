from official_news import parse_steam_news_payload

def test_official_link_allowed():
    item = parse_steam_news_payload({"appnews": {"newsitems": [{"title": "Patch", "url": "https://store.steampowered.com/news/app/1361210/view/123", "date": 0}]}})
    assert item and item["title"] == "Patch"

def test_external_link_rejected():
    item = parse_steam_news_payload({"appnews": {"newsitems": [{"title": "Patch", "url": "https://example.com/phishing", "date": 0}]}})
    assert item is None

def test_insecure_link_rejected():
    item = parse_steam_news_payload({"appnews": {"newsitems": [{"title": "Patch", "url": "http://store.steampowered.com/news/app/1361210", "date": 0}]}})
    assert item is None
