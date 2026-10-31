import pytest

from newsbot.text import clean_html, first_sentences, truncate, url_key, utf16_len


class TestCleanHtml:
    def test_strips_tags_and_decodes_entities(self):
        assert clean_html("<p>Hello &amp; <b>world</b></p>") == "Hello & world"

    def test_collapses_whitespace(self):
        assert clean_html("a\n\n  b\tc") == "a b c"

    def test_block_tags_become_spaces(self):
        assert clean_html("<p>One.</p><p>Two.</p>") == "One. Two."

    def test_removes_wordpress_footer(self):
        html = "<p>Big news today.</p><p>The post Big News appeared first on Site.</p>"
        assert clean_html(html) == "Big news today."

    @pytest.mark.parametrize(
        "html, expected",
        [
            ("Story text here. Continue reading…", "Story text here."),
            ("Story text here. Read more", "Story text here."),
            ("Story text [&#8230;]", "Story text"),
            ("Story text [...]", "Story text"),
        ],
    )
    def test_removes_trailing_read_more(self, html, expected):
        assert clean_html(html) == expected

    def test_drops_script_and_style(self):
        assert clean_html("<style>p{color:red}</style><script>x()</script>Hi") == "Hi"

    @pytest.mark.parametrize("value", ["", None])
    def test_empty(self, value):
        assert clean_html(value) == ""


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("short", 10) == "short"

    def test_cuts_at_word_boundary(self):
        assert truncate("one two three four", 12) == "one two…"

    def test_single_long_word(self):
        assert truncate("abcdefghij", 5) == "abcd…"

    def test_drops_punctuation_before_ellipsis(self):
        assert truncate("Hello, world again", 8) == "Hello…"

    def test_result_never_exceeds_limit(self):
        text = "word " * 100
        for limit in (1, 2, 7, 50, 333):
            assert utf16_len(truncate(text, limit)) <= limit

    def test_counts_wide_characters_as_two(self):
        wide = "\U0001d538" * 10  # mathematical A, outside the basic plane
        assert utf16_len(wide) == 20
        assert utf16_len(truncate(wide, 9)) <= 9

    def test_zero_limit(self):
        assert truncate("anything", 0) == ""


class TestUrlKey:
    def test_drops_tracking_scheme_www_fragment_and_slash(self):
        assert url_key("http://www.Example.com/a/?utm_source=x&id=5#top") == "example.com/a?id=5"

    def test_http_and_https_match(self):
        assert url_key("http://site.com/x") == url_key("https://site.com/x")

    def test_keeps_meaningful_query(self):
        assert url_key("https://site.com/news?id=7&utm_medium=rss&fbclid=1") == "site.com/news?id=7"

    def test_root_path(self):
        assert url_key("https://site.com/") == "site.com/"


def test_odd_markup_never_crashes():
    # Python 3.10's HTMLParser raises AssertionError on "<![" followed by a non-keyword.
    assert "Odd title" in clean_html("<![ b Odd title")


class TestFirstSentences:
    def test_keeps_whole_sentences_that_fit(self):
        assert first_sentences("One two. Three four. Five six seven.", 20) == "One two. Three four."

    def test_long_first_sentence_is_cut_at_a_word(self):
        result = first_sentences("word " * 50, 20)
        assert result.endswith("…") and len(result) <= 20

    def test_short_text_is_unchanged(self):
        assert first_sentences("Just one.", 200) == "Just one."

    def test_initials_do_not_end_a_sentence(self):
        assert first_sentences("The U.S. government banned it. More text follows here.", 35) == (
            "The U.S. government banned it."
        )
