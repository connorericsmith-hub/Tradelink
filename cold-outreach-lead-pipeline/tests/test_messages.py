"""Merge-field completeness. A lead never gets a message with a hole in it."""
import pytest

from leadpipe.messages import MessageRenderer


@pytest.fixture
def renderer(cfg):
    return MessageRenderer(cfg)


def _lead(**kw):
    base = {"phone": "+15555550100", "company": "Example Co", "city": "Springfield",
            "state": "TX", "category_value": ""}
    base.update(kw)
    return base


def test_lead_with_a_value_routes_to_the_personalized_sequence(renderer):
    assert renderer.route(_lead(category_value="kitchen remodel")).name == "personalized"


def test_lead_without_a_value_routes_to_the_generic_sequence(renderer):
    assert renderer.route(_lead()).name == "generic"


def test_the_personalized_sequence_is_structurally_unreachable_without_the_field(renderer):
    """This is the guarantee. Not a fallback string — the lead never enters."""
    qa = renderer.qa_lead(_lead(category_value=""))
    assert qa["sequence"] == "generic"
    for message in qa["messages"]:
        assert "{category_value}" not in message["body"]
        assert not message["missing_fields"]


def test_whitespace_only_value_counts_as_missing(renderer):
    """'  ' would render as a blank slot just as surely as ''."""
    assert renderer.route(_lead(category_value="   ")).name == "generic"


def test_missing_field_is_reported_not_blanked(cfg):
    """If a sequence somehow merges a field the lead lacks, QA fails loudly."""
    cfg.data["messages"]["sequences"] = [
        {"name": "broken", "requires": [],
         "messages": [{"delay": "0m", "body": "saw your {category_value} work"}]}
    ]
    renderer = MessageRenderer(cfg)
    qa = renderer.qa_lead(_lead(category_value=""))
    assert not qa["ok"]
    assert "category_value" in qa["problems"][0]
    # The placeholder survives rather than collapsing to a blank, so the fault
    # is visible in the output.
    assert "{category_value}" in qa["messages"][0]["body"]


def test_lead_matching_no_sequence_is_refused(cfg):
    cfg.data["messages"]["sequences"] = [
        {"name": "only", "requires": ["category_value"],
         "messages": [{"delay": "0m", "body": "hi {category_value}"}]}
    ]
    renderer = MessageRenderer(cfg)
    qa = renderer.qa_lead(_lead(category_value=""))
    assert not qa["ok"]
    assert qa["reason"] == "no_sequence"


def test_encoding_fault_fails_qa(cfg):
    cfg.data["messages"]["encoding"]["auto_sanitize"] = False
    cfg.data["messages"]["sequences"] = [
        {"name": "curly", "requires": [],
         "messages": [{"delay": "0m", "body": "it’s a problem"}]}
    ]
    renderer = MessageRenderer(cfg)
    qa = renderer.qa_lead(_lead())
    assert not qa["ok"]
    assert "non-GSM-7" in qa["problems"][0]


def test_auto_sanitize_rescues_a_curly_quote(cfg):
    cfg.data["messages"]["encoding"]["auto_sanitize"] = True
    cfg.data["messages"]["sequences"] = [
        {"name": "curly", "requires": [],
         "messages": [{"delay": "0m", "body": "it’s fine"}]}
    ]
    renderer = MessageRenderer(cfg)
    qa = renderer.qa_lead(_lead())
    assert qa["ok"]
    assert qa["messages"][0]["body"] == "it's fine"


def test_emoji_fail_qa_by_default(cfg):
    cfg.data["messages"]["sequences"] = [
        {"name": "emoji", "requires": [],
         "messages": [{"delay": "0m", "body": "nice \U0001F600"}]}
    ]
    renderer = MessageRenderer(cfg)
    assert not renderer.qa_lead(_lead())["ok"]


def test_shipped_templates_are_clean(renderer):
    for report in renderer.qa_templates():
        assert report["ok"], report
        assert not report["unknown_fields"]
        assert report["segments"] == 1, (
            f"{report['sequence']} message {report['index']} is "
            f"{report['segments']} segments"
        )


def test_sequences_are_tried_in_config_order(cfg):
    cfg.data["messages"]["sequences"] = [
        {"name": "first", "requires": [], "messages": [{"body": "a"}]},
        {"name": "second", "requires": [], "messages": [{"body": "b"}]},
    ]
    assert MessageRenderer(cfg).route(_lead()).name == "first"
