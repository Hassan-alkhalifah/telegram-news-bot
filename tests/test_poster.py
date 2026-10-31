import pytest

from newsbot.poster import post_article
from newsbot.telegram import FatalTelegramError, TelegramError, UncertainDeliveryError
from tests.helpers import make_article


class FakeClient:
    def __init__(self, photo_error=None):
        self.calls = []
        self.photo_error = photo_error

    def send_photo(self, chat_id, photo, caption, buttons=None):
        self.calls.append(("photo", photo))
        if self.photo_error:
            raise self.photo_error
        return {}

    def send_message(self, chat_id, text, preview_url=None, buttons=None):
        self.calls.append(("text", preview_url))
        return {}


def test_article_with_image_is_sent_as_photo():
    client = FakeClient()
    article = make_article(image_url="https://cdn.com/i.jpg")
    assert post_article(client, "@c", article, "cap") == "photo"
    assert client.calls == [("photo", "https://cdn.com/i.jpg")]


def test_bad_image_falls_back_to_text_with_preview():
    client = FakeClient(photo_error=TelegramError("wrong type of the web page content"))
    article = make_article(url="https://site.com/a", image_url="https://cdn.com/i.webp")
    assert post_article(client, "@c", article, "cap") == "text"
    assert client.calls == [("photo", "https://cdn.com/i.webp"), ("text", "https://site.com/a")]


def test_setup_problem_is_not_hidden_by_fallback():
    client = FakeClient(photo_error=FatalTelegramError("bot is not an admin"))
    with pytest.raises(FatalTelegramError):
        post_article(client, "@c", make_article(), "cap")
    assert len(client.calls) == 1


def test_unclear_result_is_not_sent_again_as_text():
    client = FakeClient(
        photo_error=UncertainDeliveryError("no answer; the post may already be in the channel")
    )
    with pytest.raises(UncertainDeliveryError):
        post_article(client, "@c", make_article(), "cap")
    assert len(client.calls) == 1


def test_article_without_image_is_sent_as_text():
    client = FakeClient()
    article = make_article(url="https://site.com/b", image_url=None)
    assert post_article(client, "@c", article, "cap") == "text"
    assert client.calls == [("text", "https://site.com/b")]


class BannerClient(FakeClient):
    def __init__(self, failures=0):
        super().__init__()
        self.failures = failures

    def send_photo(self, chat_id, photo, caption, buttons=None):
        self.calls.append(("photo", "banner" if isinstance(photo, bytes) else photo))
        if self.failures:
            self.failures -= 1
            raise TelegramError("photo not accepted")
        return {}

    def send_message(self, chat_id, text, preview_url=None, buttons=None):
        self.calls.append(("text", preview_url))
        return {}


def test_banner_is_sent_first():
    client = BannerClient()
    assert post_article(client, "@c", make_article(), "cap", banner=b"jpeg") == "banner"
    assert client.calls == [("photo", "banner")]


def test_banner_failure_falls_back_to_the_news_photo_then_text():
    client = BannerClient(failures=2)
    article = make_article(url="https://site.com/a", image_url="https://cdn.com/i.jpg")
    assert post_article(client, "@c", article, "cap", banner=b"jpeg") == "text"
    assert client.calls == [
        ("photo", "banner"),
        ("photo", "https://cdn.com/i.jpg"),
        ("text", "https://site.com/a"),
    ]
