"""GSM-7 detection. One invisible character triples the send cost."""
from leadpipe import gsm7


def test_plain_ascii_is_gsm7():
    assert gsm7.is_gsm7("hi, this is a normal message. no problem here.")


def test_curly_apostrophe_breaks_gsm7():
    """The classic: pasted from a word processor, invisible on screen."""
    body = "it’s a normal looking message"
    assert not gsm7.is_gsm7(body)
    assert "’" in gsm7.non_gsm7_chars(body)


def test_sanitize_fixes_the_substitutable_ones():
    body = "it’s “fine” – really…"
    clean, replaced = gsm7.sanitize(body)
    assert clean == "it's \"fine\" - really..."
    assert gsm7.is_gsm7(clean)
    assert len(replaced) == 5


def test_sanitize_reports_what_it_changed():
    _, replaced = gsm7.sanitize("don’t")
    assert replaced == [("’", "'")]


def test_nonbreaking_space_is_caught():
    assert not gsm7.is_gsm7("hello world")
    clean, _ = gsm7.sanitize("hello world")
    assert clean == "hello world"


def test_zero_width_characters_are_stripped():
    clean, _ = gsm7.sanitize("he​llo")
    assert clean == "hello"


def test_emoji_are_never_gsm7():
    result = gsm7.check("nice work \U0001F600", allow_emoji=False)
    assert not result["ok"]
    assert result["emoji"]
    assert result["encoding"] == "UCS-2"


def test_emoji_allowed_when_opted_in():
    result = gsm7.check("nice work \U0001F600", allow_emoji=True)
    assert result["ok"]
    assert result["encoding"] == "UCS-2"  # still UCS-2, just accepted


def test_segment_maths_gsm7():
    assert gsm7.segments("a" * 160) == ("GSM-7", 1)
    assert gsm7.segments("a" * 161) == ("GSM-7", 2)
    assert gsm7.segments("a" * 306) == ("GSM-7", 2)
    assert gsm7.segments("a" * 307) == ("GSM-7", 3)


def test_segment_maths_ucs2_is_much_worse():
    """The whole reason to care: the same length costs far more in UCS-2."""
    body = "a" * 150
    assert gsm7.segments(body) == ("GSM-7", 1)
    assert gsm7.segments(body + "\U0001F600")[0] == "UCS-2"
    assert gsm7.segments(body + "\U0001F600")[1] >= 3


def test_extension_characters_cost_two():
    assert gsm7.encoded_length("[]") == 4
    assert gsm7.encoded_length("ab") == 2
    assert gsm7.is_gsm7("price: 10€")  # euro is in the extension table


def test_check_returns_the_sanitized_body():
    result = gsm7.check("don’t", auto_sanitize=True)
    assert result["body"] == "don't"
    assert result["ok"]


def test_check_without_sanitize_reports_the_fault():
    result = gsm7.check("don’t", auto_sanitize=False)
    assert not result["ok"]
    assert result["other_non_gsm7"] == ["’"]


def test_describe_shows_codepoints():
    assert "U+2019" in gsm7.describe(["’"])
