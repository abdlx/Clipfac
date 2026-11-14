"""Ported SupoClip ASS builders (clipfac.supoclip_captions)."""
from pathlib import Path

import pytest

from clipfac import supoclip_captions as sc
from clipfac.caption_templates import get_template

WORDS = [
    {"text": "Watch", "start": 0.2, "end": 0.5},
    {"text": "this", "start": 0.5, "end": 0.7},
    {"text": "insane", "start": 0.7, "end": 1.1},
    {"text": "play", "start": 1.1, "end": 1.5},
    {"text": "right", "start": 1.5, "end": 1.8},
    {"text": "now", "start": 1.8, "end": 2.2},
]


def test_ass_timestamp():
    assert sc.ass_timestamp(0) == "0:00:00.00"
    assert sc.ass_timestamp(61.5) == "0:01:01.50"
    assert sc.ass_timestamp(3661.25) == "1:01:01.25"
    assert sc.ass_timestamp(-3) == "0:00:00.00"


def test_hex_to_ass_color():
    # &HAABBGGRR: white opaque, yellow, 8-digit alpha.
    assert sc.hex_to_ass_color("#FFFFFF") == "&H00FFFFFF&"
    assert sc.hex_to_ass_color("#FFE000") == "&H0000E0FF&"
    assert sc.hex_to_ass_color("#00000080") == "&H7F000000&"
    assert sc.hex_to_ass_color("not-a-color") == "&H00FFFFFF&"


def test_escape_ass_text():
    assert sc.escape_ass_text("a{b}\\c") == "a\\{b\\}\\\\c"


def test_fonts_resolve_from_clipfac_fonts_dir():
    assert sc.find_font_path("THEBOLDFONT").name == "THEBOLDFONT.ttf"
    assert sc.find_font_path("TikTokSans-Bold").name == "TikTokSans-Bold.ttf"
    assert sc.find_font_path("No Such Font") is None
    assert sc.get_font_family_name(
        sc.find_font_path("THEBOLDFONT")) == "THE BOLD FONT FREE VERSION"
    metrics = sc.get_font_advance_metrics(sc.find_font_path("THEBOLDFONT"))
    assert metrics is not None
    assert metrics.text_width("HELLO", 60) > metrics.text_width("HI", 60)
    assert 0 < sc.get_scaled_font_size(32, 1080) <= 132


def test_hook_title_builder(tmp_path):
    style, events = sc.build_hook_title_ass(
        "POV: 24 kills one mag", get_template("hormozi"),
        1080, 1920, 30.0, "THE BOLD FONT FREE VERSION", 62)
    assert style.startswith("Style: Hook,THE BOLD FONT FREE VERSION,")
    assert len(events) == 1
    assert "POV" in events[0] and "24" in events[0]
    # Power word / number gets the template highlight colour inline.
    assert "\\c&H0066FF00&" in events[0] or "\\c&H" in events[0]
    assert events[0].startswith("Dialogue: 1,0:00:00.12,")


def test_full_builder_hormozi_karaoke(tmp_path):
    out = tmp_path / "hormozi.ass"
    assert sc.build_assemblyai_ass_subtitles(
        1080, 1920, out, words=WORDS, clip_duration=6.0,
        caption_template="hormozi", hook_title="POV: 24 kills one mag")
    text = out.read_text(encoding="utf-8")
    assert "Style: Hook," in text
    assert "Style: Default," in text
    # 6 words in chunks of 3 -> 2 chunks x 3 word-events + 1 hook event.
    assert text.count("Dialogue:") == 7
    # Hormozi green highlight (#00FF66 -> BBGGRR 66FF00) + green word pill
    # (#00BF49 -> BBGGRR 49BF00).
    assert "0066FF00" in text
    assert "0049BF00" in text
    # Uppercase template: the spoken words are uppercased in the file.
    assert "INSANE" in text


def test_full_builder_minimal_fade_one_event_per_chunk(tmp_path):
    out = tmp_path / "minimal.ass"
    assert sc.build_assemblyai_ass_subtitles(
        1080, 1920, out, words=WORDS, clip_duration=6.0,
        caption_template="minimal")
    text = out.read_text(encoding="utf-8")
    # fade animation: one event per chunk (6 words, max 6/line -> 1 chunk).
    assert text.count("Dialogue:") == 1
    assert "\\fad(120,120)" in text


def test_builder_accepts_clipfac_word_key_and_empty_input(tmp_path):
    clipfac_words = [{"word": " hello", "start": 0.0, "end": 0.4},
                     {"word": " world", "start": 0.4, "end": 0.9}]
    out = tmp_path / "clipfac.ass"
    assert sc.build_assemblyai_ass_subtitles(
        1080, 1920, out, words=clipfac_words, clip_duration=6.0)
    assert "hello" in out.read_text(encoding="utf-8").lower()

    # No words and no hook: nothing to burn.
    out2 = tmp_path / "empty.ass"
    assert sc.build_assemblyai_ass_subtitles(
        1080, 1920, out2, words=[], clip_duration=6.0) is False
    # Hook-only burn works with no words at all.
    out3 = tmp_path / "hookonly.ass"
    assert sc.build_assemblyai_ass_subtitles(
        1080, 1920, out3, words=[], clip_duration=6.0,
        hook_title="Just the title", include_captions=False)
    assert out3.read_text(encoding="utf-8").count("Dialogue:") == 1


def test_emoji_never_injected_in_clipfac(tmp_path):
    # Clipfac has no colour-emoji font, so even emoji templates stay text-only.
    assert sc.emoji_rendering_supported() is False
    out = tmp_path / "noemoji.ass"
    sc.build_assemblyai_ass_subtitles(
        1080, 1920, out, words=WORDS, clip_duration=6.0,
        caption_template="default")
    assert "Noto Color Emoji" not in out.read_text(encoding="utf-8")
