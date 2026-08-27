"""`GET /videos/<id>/export.md` — the owner's Markdown copy of one video.

Two things are worth more than the rest here. The first is the gate: the
document is the whole transcript, so on a public instance it has to be refused
to the anonymous request that `VIDTHEQUE_AUTH=none` makes look like everyone
else, while a private box keeps the `/frames` rule and stays open. The second is
that every transcript line carries a `?t=` computed by the one helper, so an
exported file cites the same second the tools do.
"""

from __future__ import annotations

import ipaddress
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.config import Settings
from vidtheque_mcp.dashboard.settings import DashboardSettings
from vidtheque_mcp.public.settings import PublicSettings

from .conftest import FakeEmbeddings, seed

VIDEO_ID = "kCc8FmEb1nY"  # "Let's build GPT", 6 cues, 2 OCR frames
NO_FRAMES_ID = "eMlx5fFNoYc"  # stt/chunk/text_embed only — no OCR, no frames


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    return data


def make_client(
    data: Path,
    public: PublicSettings | None = None,
    dashboard: DashboardSettings | None = None,
    peer: tuple[str, int] | None = None,
    **overrides,
) -> TestClient:
    base = {
        "data_dir": data,
        "public_url": "http://localhost:8080",
        "worker_url": "http://worker:8081",
        "auth_mode": "none",
        "secret": "test-secret",
    }
    base.update(overrides)
    settings = Settings(**base)  # type: ignore[arg-type]
    app = build_app(
        settings,
        embeddings=FakeEmbeddings(),
        run_pipeline=False,
        public=public or PublicSettings(enabled=False),
        dashboard=dashboard,
    )
    if peer is None:
        return TestClient(app, base_url=settings.public_url)
    return TestClient(app, base_url=settings.public_url, client=peer)


def get(data: Path, path: str, public: PublicSettings | None = None, **kwargs):
    with make_client(data, public, **kwargs) as client:
        return client.get(path)


# ------------------------------------------------------------------- the gate


def test_a_private_box_in_none_mode_serves_the_export(corpus: Path) -> None:
    """The README quickstart deployment. Open, because everything else is."""
    response = get(corpus, f"/videos/{VIDEO_ID}/export.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")


def test_the_public_projection_refuses_an_anonymous_export(corpus: Path) -> None:
    """`AUTH=none` makes every request "open" — that must not mean "the owner".

    Without this the demo is a bulk download of every transcript in the corpus.
    """
    response = get(corpus, f"/videos/{VIDEO_ID}/export.md", PublicSettings(enabled=True))
    assert response.status_code == 403
    assert response.json()["error"] == "E_FORBIDDEN"
    assert "no-store" in response.headers["cache-control"]


def test_the_public_projection_serves_a_trusted_peer(corpus: Path) -> None:
    """The one lever that gives an `AUTH=none` deployment its owner back."""
    response = get(
        corpus,
        f"/videos/{VIDEO_ID}/export.md",
        PublicSettings(enabled=True),
        dashboard=DashboardSettings(trusted_cidrs=(ipaddress.ip_network("10.0.0.0/8"),)),
        peer=("10.9.9.9", 4444),
    )
    assert response.status_code == 200
    assert response.text.startswith("---\n")


def test_token_mode_challenges_an_anonymous_export(corpus: Path) -> None:
    response = get(corpus, f"/videos/{VIDEO_ID}/export.md", auth_mode="token", static_token="t0k")
    assert response.status_code == 401
    assert 'Bearer error="invalid_token"' in response.headers["www-authenticate"]


def test_token_mode_serves_a_bearer(corpus: Path) -> None:
    with make_client(corpus, auth_mode="token", static_token="t0k") as client:
        response = client.get(
            f"/videos/{VIDEO_ID}/export.md", headers={"Authorization": "Bearer t0k"}
        )
    assert response.status_code == 200


# ------------------------------------------------------------- what it refuses


def test_an_unknown_video_is_a_404(corpus: Path) -> None:
    response = get(corpus, "/videos/aaaaaaaaaaa/export.md")
    assert response.status_code == 404
    assert response.json()["error"] == "E_UNKNOWN_VIDEO"


def test_a_malformed_id_is_a_404_without_touching_the_database(corpus: Path) -> None:
    response = get(corpus, "/videos/not-an-id/export.md")
    assert response.status_code == 404
    assert response.json()["error"] == "E_UNKNOWN_VIDEO"


def test_a_video_the_pipeline_never_ran_is_a_409(corpus: Path) -> None:
    conn = sqlite3.connect(corpus / "vidtheque.db")
    conn.execute("UPDATE videos SET index_state = 'pending' WHERE source_id = ?", (VIDEO_ID,))
    conn.commit()
    conn.close()

    response = get(corpus, f"/videos/{VIDEO_ID}/export.md")
    assert response.status_code == 409
    assert response.json()["error"] == "E_NOT_INDEXED"


# ------------------------------------------------------------------- the file


def test_the_front_matter_carries_what_a_notes_app_indexes_on(corpus: Path) -> None:
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md").text
    head = body.split("---", 2)[1]
    assert 'title: "Let\'s build GPT: from scratch"' in head
    assert f'video_id: "{VIDEO_ID}"' in head
    assert 'channel: "Andrej Karpathy"' in head
    assert "published: 2023-01-17" in head
    assert 'duration: "1:56:40"' in head
    assert "exported_by: \"vidtheque " in head


def test_every_transcript_line_carries_the_second_it_was_said(corpus: Path) -> None:
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md").text
    transcript = body.split("## Transcript", 1)[1]
    # The first cue starts at 0.0 and the lead cannot take it below zero; the
    # sixth is at 420.0, so it is 418 after the two-second lead every other
    # surface applies.
    assert f"[0:00](https://youtu.be/{VIDEO_ID}?t=0)" in transcript
    assert f"[7:00](https://youtu.be/{VIDEO_ID}?t=418)" in transcript
    assert "we cache the keys and the values at every new token" in transcript
    assert "much later we talk about tokenization instead" in transcript


def test_the_whole_transcript_is_there_not_a_page_of_it(corpus: Path) -> None:
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md").text
    transcript = body.split("## Transcript", 1)[1].split("## On-screen text", 1)[0]
    assert transcript.count("](https://youtu.be/") == 6


def test_the_stage_table_says_what_the_file_was_made_of(corpus: Path) -> None:
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md").text
    assert "## How this was indexed" in body
    assert "| stt |" in body
    assert "| frame_embed |" in body


def test_on_screen_text_arrives_in_the_order_it_appeared(corpus: Path) -> None:
    """`ocr_highlights` ranks by how much text a frame carries; a talk is a line."""
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md").text
    section = body.split("## On-screen text", 1)[1]
    early = section.index("kv cache size")
    late = section.index("nvidia-smi")
    assert early < late, "the 5 s frame must precede the 430 s one"
    assert f"`{VIDEO_ID}-00000`" in section


def test_ocr_zero_omits_the_section_entirely(corpus: Path) -> None:
    body = get(corpus, f"/videos/{VIDEO_ID}/export.md?ocr=0").text
    assert "## Transcript" in body
    assert "## On-screen text" not in body


def test_a_video_with_no_frames_says_so_rather_than_ending(corpus: Path) -> None:
    body = get(corpus, f"/videos/{NO_FRAMES_ID}/export.md").text
    assert "## On-screen text" in body
    assert "No on-screen text is indexed" in body


def test_the_response_is_a_download_named_for_the_video(corpus: Path) -> None:
    response = get(corpus, f"/videos/{VIDEO_ID}/export.md")
    assert response.headers["content-disposition"] == f'attachment; filename="{VIDEO_ID}.md"'
    assert "no-store" in response.headers["cache-control"]
