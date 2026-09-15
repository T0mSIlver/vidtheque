from __future__ import annotations

from vidtheque_mcp.editions import Edition, Session, Speaker, build_context_bias, load_edition


def test_committed_fixture_pins_priority_and_cap() -> None:
    bias = build_context_bias(load_edition())
    assert len(bias) == 100
    assert bias[:3] == ["Clemens Rawert", "Lélio Renard Lavaud", "Jakob Pörschmann"]
    assert bias[34:38] == ["Langfuse", "Mistral", "Black Forest Labs", "ElevenLabs"]
    assert bias[66:76] == [
        "vLLM",
        "SGLang",
        "pyannote",
        "RRF",
        "LoRA",
        "KV cache",
        "MCP",
        "FlashAttention",
        "Voxtral",
        "Qwen",
    ]
    assert bias[-4:] == ["Loads", "Optimizing", "Small", "VideoLLM"]


def test_nfkc_casefold_dedup_keeps_the_first_spelling() -> None:
    edition = Edition(
        slug="test",
        edition_tag="series:test",
        fixed_context_bias=("MISTRAL", "Qwen"),
        sessions=(
            Session("Unique Quartz", (Speaker("Alice Smith", "Mistral"),)),
            Session("Other Quartz", (Speaker("Ａlice Smith", "MISTRAL"),)),
        ),
    )
    bias = build_context_bias(edition)
    assert bias[:3] == ["Alice Smith", "Mistral", "Qwen"]
    assert bias.count("Mistral") == 1
    assert "Quartz" not in bias
