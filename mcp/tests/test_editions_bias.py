from __future__ import annotations

from vidtheque_mcp.editions import build_context_bias, load_edition


def test_committed_fixture_pins_priority_and_cap() -> None:
    bias = build_context_bias(load_edition("aie-paris-2026"))
    assert len(bias) == 100
    assert bias[:3] == ["Jen Person", "Lia McBride", "Lara Khanafer"]
    assert bias[56:60] == ["Mistral", "AI Engineer", "STATION F", "Langfuse"]
    # 56 speakers and their companies leave room for half the fixed terms
    assert bias[95:100] == ["SGLang", "pyannote", "RRF", "LoRA", "KV cache"]
    # Voxtral wrote "model" as "Modal" while the company was in the list
    assert "Modal" not in bias


def test_an_excluded_term_is_added_by_no_tier() -> None:
    edition = {
        "slug": "test",
        "tags": {"edition": "series:test"},
        "context_bias": {"fixed": ["modal"], "exclude": ["Modal"]},
        "sessions": [
            {"title": "Modal Serving", "speakers": [{"name": "Ann Lee", "company": "Modal"}]},
        ],
    }
    assert build_context_bias(edition) == ["Ann Lee", "Serving"]


def test_speaker_names_alone_can_fill_the_cap_and_later_tiers_add_nothing() -> None:
    edition = {
        "slug": "test",
        "tags": {"edition": "series:test"},
        "context_bias": {"fixed": ["Voxtral"]},
        "sessions": [
            {
                "title": f"Distinctive Session {index}",
                "speakers": [{"name": f"Speaker Number{index}", "company": "Mistral"}],
            }
            for index in range(120)
        ],
    }
    bias = build_context_bias(edition)
    assert len(bias) == 100
    assert bias[0] == "Speaker Number0" and bias[-1] == "Speaker Number99"
    assert "Mistral" not in bias and "Voxtral" not in bias


def test_nfkc_casefold_dedup_keeps_the_first_spelling() -> None:
    edition = {
        "slug": "test",
        "tags": {"edition": "series:test"},
        "context_bias": {"fixed": ["MISTRAL", "Qwen"]},
        "sessions": [
            {
                "title": "Unique Quartz",
                "speakers": [{"name": "Alice Smith", "company": "Mistral"}],
            },
            {
                "title": "Other Quartz",
                "speakers": [{"name": "Ａlice Smith", "company": "MISTRAL"}],
            },
        ],
    }
    bias = build_context_bias(edition)
    assert bias[:3] == ["Alice Smith", "Mistral", "Qwen"]
    assert bias.count("Mistral") == 1
    assert "Quartz" not in bias
