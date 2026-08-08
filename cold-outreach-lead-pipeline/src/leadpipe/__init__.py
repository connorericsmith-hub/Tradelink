"""cold-outreach-lead-pipeline — find, qualify and prepare local business leads.

Six stages, config-driven end to end:

    scrape -> filter -> normalize -> categorize -> score -> export

Nothing in this package knows what niche you are targeting. Every keyword,
deny term, score weight, category value and word of message copy lives in one
YAML file. See config.example.yaml.
"""

__version__ = "1.0.0"
