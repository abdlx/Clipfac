"""Word-level animated captions + hook-title burn-in — PORT of SupoClip
``backend/src/media/captions.py`` (build_hook_title_ass,
build_assemblyai_ass_subtitles and the helpers they need), adapted to
Clipfac's pipeline. 2026-11-13, per singham's order to lift SupoClip code.

What is verbatim (modulo imports): the ASS builders, the colour/timestamp
helpers, the title line-balancing, the caption chunking/measuring, and the
pure-Python TTF parsing from SupoClip's ``font_registry`` (family name,
codepoints, advance metrics — no fontTools needed).

What is adapted, and why:
- ``FONTS_DIR`` is Clipfac's repo ``fonts/`` (THEBOLDFONT + TikTokSans were
  copied there from SupoClip's backend fonts). The font lookup drops
  SupoClip's per-user font machinery; only bundled fonts are searched.
- ``build_assemblyai_ass_subtitles`` no longer loads a cached AssemblyAI
  transcript from ``video_path``. Clipfac passes clip-relative words from its
  own transcript (``recut.virtual_transcript`` in the caller) as ``words`` —
  each ``{"text"|"word", "start", "end"}`` — plus ``clip_duration``. The
  ``keep_ranges`` / ``output_offsets`` / transcript-loading branches are
  dropped; passage breaks only ever came from ``keep_ranges``, so chunking
  without them is unchanged behaviour.
- ``emoji_rendering_supported()`` always returns False here: Clipfac bundles
  no colour-emoji font (SupoClip's probe also needs cv2, which this env
  lacks), so templates with ``emoji: True`` still get keyword emphasis but
  no emoji glyphs — never tofu boxes. Bundle a colour-emoji font and flip
  this to probe again.
- ``build_point_label_ass`` is ported whole for completeness (recap labels);
  nothing in Clipfac calls it yet.
- Logging goes through this module's logger, not SupoClip's media logger.

Burning: the .ass this module writes is burned with Clipfac's existing
``subtitles.burn_subtitles`` (``ass=`` filter, ``fontsdir`` = repo fonts/),
exactly like Clipfac's own karaoke ASS.
"""

import logging
import re
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from clipfac.caption_templates import get_template
from clipfac.emoji_captions import POWER_WORDS, annotate_caption_words, normalize_token

logger = logging.getLogger(__name__)

# --- hook-title layout constants (SupoClip src/media/common.py) -----------
HOOK_TITLE_SECONDS = 4.0
HOOK_TITLE_MIN_SECONDS = 1.5
HOOK_TITLE_SIDE_MARGIN_FRAC = 0.07
HOOK_TITLE_TOP_MARGIN_FRAC = 0.12

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
SUPPORTED_FONT_EXTENSIONS = (".ttf", ".otf")
# SupoClip's colour-emoji font name; no such font is bundled in Clipfac, so
# emoji injection stays off (see emoji_rendering_supported).
EMOJI_FONT_NAME = "Noto Color Emoji"

TTF_NAME_ID_FONT_FAMILY = 1
TTF_NAME_ID_FULL_NAME = 4
ASS_UNSAFE_NAME_CHARS = frozenset("(){}\\")
# Bundled font used when the chosen one lacks glyphs for the clip's text. Its
# width is close to THEBOLDFONT's, so hook-title line wrapping still fits.
GLYPH_FALLBACK_FONT = "TikTokSans-Bold"
# These draw lowercase letters as capitals; their fallback is uppercased so the
# clip keeps the same all-caps look.
CAPS_ONLY_FONTS = frozenset({"THEBOLDFONT"})


# ---------------------------------------------------------------------------
# Font lookup + pure-Python TTF parsing (ported from SupoClip font_registry)
# ---------------------------------------------------------------------------

def _collect_bundled_fonts():
    out = []
    if not FONTS_DIR.is_dir():
        return out
    for path in sorted(FONTS_DIR.iterdir()):
        if path.suffix.lower() in SUPPORTED_FONT_EXTENSIONS:
            out.append({"name": get_font_family_name(path) or path.stem,
                        "file_path": str(path)})
    return out


def find_font_path(font_name: str) -> Optional[Path]:
    """Find a bundled font by file name, stem, or family name."""
    requested = (font_name or "").strip()
    if not requested:
        return None
    exact_file = FONTS_DIR / requested
    if exact_file.exists() and exact_file.suffix.lower() in SUPPORTED_FONT_EXTENSIONS:
        return exact_file
    for extension in SUPPORTED_FONT_EXTENSIONS:
        candidate = FONTS_DIR / f"{requested}{extension}"
        if candidate.exists():
            return candidate
    normalized_requested = re.sub(r"[^a-z0-9]", "", requested.lower())
    for font in _collect_bundled_fonts():
        normalized_name = re.sub(r"[^a-z0-9]", "", font["name"].lower())
        if normalized_requested == normalized_name:
            return Path(font["file_path"])
    return None


def _decode_ttf_name(raw_value: bytes, platform_id: int) -> str:
    encodings = ["utf-16-be"] if platform_id in {0, 3} else ["mac_roman", "utf-8"]
    for encoding in encodings:
        try:
            decoded = raw_value.decode(encoding).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return ""


def get_font_family_name(font_path: Path) -> Optional[str]:
    """Read a font's internal family name for renderers that match by metadata."""
    try:
        data = Path(font_path).read_bytes()
        if len(data) < 12:
            return None

        num_tables = struct.unpack_from(">H", data, 4)[0]
        name_table_offset = None
        name_table_length = None
        table_record_offset = 12
        for table_index in range(num_tables):
            record_offset = table_record_offset + table_index * 16
            if record_offset + 16 > len(data):
                return None
            tag, _checksum, table_offset, table_length = struct.unpack_from(
                ">4sIII", data, record_offset
            )
            if tag == b"name":
                name_table_offset = table_offset
                name_table_length = table_length
                break

        if name_table_offset is None or name_table_length is None:
            return None
        if name_table_offset + min(name_table_length, 6) > len(data):
            return None

        _format_selector, record_count, string_offset = struct.unpack_from(
            ">HHH", data, name_table_offset
        )
        storage_start = name_table_offset + string_offset
        candidates: dict[int, list[str]] = {
            TTF_NAME_ID_FONT_FAMILY: [],
            TTF_NAME_ID_FULL_NAME: [],
        }

        for record_index in range(record_count):
            record_offset = name_table_offset + 6 + record_index * 12
            if record_offset + 12 > len(data):
                break
            (
                platform_id,
                _encoding_id,
                language_id,
                name_id,
                value_length,
                value_offset,
            ) = struct.unpack_from(">HHHHHH", data, record_offset)
            if name_id not in candidates:
                continue
            value_start = storage_start + value_offset
            value_end = value_start + value_length
            if value_start < storage_start or value_end > len(data):
                continue
            decoded = _decode_ttf_name(data[value_start:value_end], platform_id)
            if not decoded:
                continue
            # Prefer English records when present, but keep every valid name as fallback.
            if language_id in {0x0409, 0x0000}:
                candidates[name_id].insert(0, decoded)
            else:
                candidates[name_id].append(decoded)

        family_names = list(dict.fromkeys(candidates[TTF_NAME_ID_FONT_FAMILY]))
        full_names = [name for name in candidates[TTF_NAME_ID_FULL_NAME] if name]
        # ASS override tags (\fn<name>) end at "(", "{" or "\", so a family like
        # "THE BOLD FONT (FREE VERSION)" silently falls back to another font.
        # libass also matches full names, so prefer any tag-safe candidate.
        for names in (family_names, full_names):
            safe_names = [name for name in names if not ASS_UNSAFE_NAME_CHARS & set(name)]
            if safe_names:
                return min(safe_names, key=len)

        if family_names:
            return min(family_names, key=len)
        if full_names:
            return full_names[0]
    except Exception:
        return None

    return None


def _table_offsets(data: bytes) -> dict[bytes, int]:
    num_tables = struct.unpack_from(">H", data, 4)[0]
    offsets: dict[bytes, int] = {}
    for table_index in range(num_tables):
        tag, _checksum, table_offset, _length = struct.unpack_from(
            ">4sIII", data, 12 + table_index * 16
        )
        offsets[tag] = table_offset
    return offsets


def _parse_cmap(data: bytes) -> Optional[dict[int, int]]:
    """Map Unicode codepoints to glyph ids from the font's cmap table."""
    cmap_offset = _table_offsets(data).get(b"cmap")
    if cmap_offset is None:
        return None

    subtables: dict[int, int] = {}
    _version, subtable_count = struct.unpack_from(">HH", data, cmap_offset)
    for index in range(subtable_count):
        platform_id, encoding_id, offset = struct.unpack_from(
            ">HHI", data, cmap_offset + 4 + index * 8
        )
        # Only Unicode subtables: Windows BMP/full or the Unicode platform.
        if platform_id == 0 or (platform_id == 3 and encoding_id in {1, 10}):
            fmt = struct.unpack_from(">H", data, cmap_offset + offset)[0]
            subtables.setdefault(fmt, cmap_offset + offset)

    glyphs: dict[int, int] = {}
    if 12 in subtables:
        start = subtables[12]
        group_count = struct.unpack_from(">I", data, start + 12)[0]
        for group in range(group_count):
            first, last, first_glyph = struct.unpack_from(
                ">III", data, start + 16 + group * 12
            )
            if first_glyph:
                for code in range(first, last + 1):
                    glyphs[code] = first_glyph + code - first
        return glyphs

    if 4 not in subtables:
        return None
    start = subtables[4]
    seg_count = struct.unpack_from(">H", data, start + 6)[0] // 2
    ends_at = start + 14
    starts_at = ends_at + seg_count * 2 + 2
    deltas_at = starts_at + seg_count * 2
    range_offsets_at = deltas_at + seg_count * 2
    for seg in range(seg_count):
        end_code = struct.unpack_from(">H", data, ends_at + seg * 2)[0]
        start_code = struct.unpack_from(">H", data, starts_at + seg * 2)[0]
        delta = struct.unpack_from(">h", data, deltas_at + seg * 2)[0]
        range_offset = struct.unpack_from(">H", data, range_offsets_at + seg * 2)[0]
        for code in range(start_code, min(end_code, 0xFFFE) + 1):
            if range_offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                glyph_at = (
                    range_offsets_at + seg * 2 + range_offset + (code - start_code) * 2
                )
                glyph = struct.unpack_from(">H", data, glyph_at)[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                glyphs[code] = glyph
    return glyphs


def _parse_cmap_codepoints(data: bytes) -> Optional[frozenset]:
    glyphs = _parse_cmap(data)
    return None if glyphs is None else frozenset(glyphs)


@lru_cache(maxsize=128)
def _cached_font_codepoints(path: str, modified_ns: int, size: int):
    try:
        return _parse_cmap_codepoints(Path(path).read_bytes())
    except Exception:
        return None


def get_font_codepoints(font_path: Path) -> Optional[frozenset]:
    """Unicode codepoints the font actually has glyphs for (None if unreadable).

    Renderers silently substitute another font for missing glyphs, so callers
    check coverage before committing to a font for a given text.
    """
    try:
        stat = Path(font_path).stat()
    except OSError:
        return None
    return _cached_font_codepoints(str(font_path), stat.st_mtime_ns, stat.st_size)


class FontAdvanceMetrics:
    """Horizontal advances of a font, scaled the way libass sizes ASS fonts.

    libass treats an ASS ``Fontsize`` as the font's usWinAscent + usWinDescent
    height, not its em size, so ``em_per_size`` converts one to the other.
    Kerning is ignored: it only tightens text, keeping widths conservative.
    """

    def __init__(self, em_per_size: float, advances: dict, fallback: float):
        self.em_per_size = em_per_size
        self.advances = advances  # codepoint -> advance in em
        self.fallback = fallback  # average advance, in em, for unmapped codepoints

    def text_width(self, text: str, size: float) -> float:
        em = sum(self.advances.get(ord(ch), self.fallback) for ch in text)
        return em * self.em_per_size * size


def _parse_advance_metrics(data: bytes) -> Optional[FontAdvanceMetrics]:
    tables = _table_offsets(data)
    if not {b"head", b"hhea", b"hmtx"} <= tables.keys():
        return None
    units_per_em = struct.unpack_from(">H", data, tables[b"head"] + 18)[0]
    ascender, descender = struct.unpack_from(">hh", data, tables[b"hhea"] + 4)
    metric_count = struct.unpack_from(">H", data, tables[b"hhea"] + 34)[0]
    if not units_per_em or not metric_count:
        return None
    height = ascender - descender
    if b"OS/2" in tables:
        win_ascent, win_descent = struct.unpack_from(">HH", data, tables[b"OS/2"] + 74)
        if win_ascent + win_descent:
            height = win_ascent + win_descent
    if height <= 0:
        height = units_per_em

    hmtx = tables[b"hmtx"]
    glyph_advances = [
        struct.unpack_from(">H", data, hmtx + index * 4)[0] for index in range(metric_count)
    ]
    glyphs = _parse_cmap(data) or {}
    advances = {
        code: glyph_advances[min(glyph, metric_count - 1)] / units_per_em
        for code, glyph in glyphs.items()
    }
    letters = [advances[ord(ch)] for ch in "abcdefghijklmnopqrstuvwxyz" if ord(ch) in advances]
    fallback = sum(letters) / len(letters) if letters else 0.6
    return FontAdvanceMetrics(units_per_em / height, advances, fallback)


@lru_cache(maxsize=32)
def _cached_advance_metrics(path: str, modified_ns: int, size: int):
    try:
        return _parse_advance_metrics(Path(path).read_bytes())
    except Exception:
        return None


def get_font_advance_metrics(font_path: Path) -> Optional[FontAdvanceMetrics]:
    """Advance widths for measuring rendered text (None if unreadable)."""
    try:
        stat = Path(font_path).stat()
    except OSError:
        return None
    return _cached_advance_metrics(str(font_path), stat.st_mtime_ns, stat.st_size)


# ---------------------------------------------------------------------------
# Caption geometry + ASS primitives (verbatim from SupoClip captions.py)
# ---------------------------------------------------------------------------

def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width while preserving user choices.

    Template defaults remain readable on 1080-wide vertical clips, while the
    full 12-72 UI range produces a meaningful, monotonic size change.
    """
    scaled_size = round(base_font_size * (video_width / 560.0))
    return max(26, min(132, scaled_size))


def get_subtitle_max_width(video_width: int) -> int:
    """Return max subtitle text width with horizontal safe margins."""
    horizontal_padding = max(40, int(video_width * 0.06))
    return max(200, video_width - (horizontal_padding * 2))


def caption_text_measurer(font_family: Optional[str], font_px: int):
    """Return ``measure(text) -> px`` for text rendered by libass at ``font_px``.

    Uses the font's own advance widths when the font file is available, and a
    deliberately wide per-character estimate otherwise.
    """
    font_path = find_font_path(font_family) if font_family else None
    metrics = get_font_advance_metrics(font_path) if font_path else None
    if metrics is None:
        return lambda text: len(text) * font_px * 0.62
    return lambda text: metrics.text_width(text, font_px)


def split_caption_chunk(
    widths: List[float], space_width: float, max_width: float
) -> List[Tuple[int, int]]:
    """Split one caption chunk into ``(start, end)`` runs that fit ``max_width``.

    A chunk that fits stays whole. Otherwise it is cut where the wider part is
    narrowest, and each part again, so "5,000 MILLIAMP-HOUR RECHARGEABLE
    BATTERY" becomes two balanced lines instead of one that leaves the frame.
    A single word wider than the line stays alone; the caller scales it down.
    """

    def width(start: int, end: int) -> float:
        return sum(widths[start:end]) + space_width * max(0, end - start - 1)

    def split(start: int, end: int) -> List[Tuple[int, int]]:
        if end - start <= 1 or width(start, end) <= max_width:
            return [(start, end)]
        cut = min(
            range(start + 1, end),
            key=lambda index: (max(width(start, index), width(index, end)), -index),
        )
        return split(start, cut) + split(cut, end)

    return split(0, len(widths)) if widths else []


def get_safe_vertical_position(
    video_height: int, text_height: int, position_y: float
) -> int:
    """Return subtitle y position clamped inside a top/bottom safe area."""
    min_top_padding = max(40, int(video_height * 0.05))
    min_bottom_padding = max(120, int(video_height * 0.10))

    desired_y = int(video_height * position_y - text_height // 2)
    max_y = video_height - min_bottom_padding - text_height
    return max(min_top_padding, min(desired_y, max_y))


# Spoken captions moved off burned-in source captions stay below this height,
# so they do not land on the speaker's face or under the hook title.
MIN_MOVED_CAPTION_CENTER = 0.45


def caption_center_clear_of_band(
    center_y: int, text_height: int, band: Tuple[float, float], video_height: int
) -> int:
    """Move a caption centre so its line does not overlap ``band``.

    Returns ``center_y`` unchanged when there is no overlap. Otherwise the
    caption goes just above the band, or just below it when there is no room
    above, keeping the bottom safe area clear.
    """
    gap = max(12, int(video_height * 0.015))
    half = text_height // 2
    band_top, band_bottom = int(band[0] * video_height), int(band[1] * video_height)
    if center_y + half + gap <= band_top or center_y - half - gap >= band_bottom:
        return center_y

    above = band_top - gap - half
    if above >= video_height * MIN_MOVED_CAPTION_CENTER:
        return above
    below = band_bottom + gap + half
    bottom_limit = video_height - max(120, int(video_height * 0.10)) - half
    if below <= bottom_limit:
        return below
    return max(above, int(video_height * MIN_MOVED_CAPTION_CENTER))


def emoji_rendering_supported() -> bool:
    """Whether this environment's libass renders COLOUR emojis.

    ADAPTED: always False in Clipfac — no colour-emoji font is bundled and
    the SupoClip probe needs cv2. Templates with ``emoji: True`` still get
    keyword emphasis; they just never inject emoji glyphs, so a burn can
    never show tofu boxes. (SupoClip's original probed a test burn with cv2.)
    """
    return False


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def hex_to_ass_color(
    value: Optional[str], fallback: str = "#FFFFFF", include_alpha: bool = True
) -> str:
    value = (value or fallback).strip()
    if value.startswith("#"):
        value = value[1:]
    alpha = 0
    if len(value) == 8:
        css_alpha = int(value[6:8], 16)
        alpha = 255 - css_alpha
        value = value[:6]
    if len(value) != 6:
        value = fallback.lstrip("#")
        if len(value) == 8:
            css_alpha = int(value[6:8], 16)
            alpha = 255 - css_alpha
            value = value[:6]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha_part = f"{alpha:02X}" if include_alpha else "00"
    return f"&H{alpha_part}{blue}{green}{red}&"


def escape_ass_text(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def ass_font_name(font_family: Optional[str]) -> str:
    if not font_family:
        return "Arial"
    font_path = find_font_path(font_family)
    if font_path:
        return get_font_family_name(Path(font_path)) or Path(font_path).stem
    return font_family or "Arial"


def ass_fonts_dir(font_family: Optional[str]) -> Optional[Path]:
    if not font_family:
        return FONTS_DIR if FONTS_DIR.exists() else None
    font_path = find_font_path(font_family)
    if font_path:
        return font_path.parent
    return FONTS_DIR if FONTS_DIR.exists() else None


def ensure_glyph_coverage(
    font_family: str, template: Dict[str, Any], texts: Iterable[str]
) -> Tuple[str, Dict[str, Any]]:
    """Swap to GLYPH_FALLBACK_FONT when ``font_family`` cannot draw ``texts``.

    libass silently renders missing glyphs from another font, so the free
    THEBOLDFONT (no accented letters) printed "ACTúA" with a lowercase ú taken
    from a system font. One covering font for the whole clip looks consistent.
    """
    font_path = find_font_path(font_family)
    fallback_path = find_font_path(GLYPH_FALLBACK_FONT)
    # ass_fonts_dir() points libass at the original font's folder only.
    if not font_path or not fallback_path or font_path.parent != fallback_path.parent:
        return font_family, template
    covered = get_font_codepoints(font_path)
    fallback_covered = get_font_codepoints(fallback_path)
    if not covered or not fallback_covered:
        return font_family, template

    missing = {
        ch for text in texts for ch in text if ch.isalpha() and ord(ch) not in covered
    }
    if not missing or any(ord(ch) not in fallback_covered for ch in missing):
        return font_family, template

    logger.info(
        "Font %s has no glyphs for %s; rendering captions with %s",
        font_family,
        "".join(sorted(missing)),
        GLYPH_FALLBACK_FONT,
    )
    if font_path.stem in CAPS_ONLY_FONTS:
        template = {**template, "uppercase": True}
    return GLYPH_FALLBACK_FONT, template


def _title_width(text: str) -> int:
    """Width in average Latin characters: a Han, kana or Hangul character is about two."""
    return sum(2 if unicodedata.east_asian_width(character) in "WF" else 1 for character in text)


def _title_lines(title: str, max_chars: int) -> List[str]:
    """One or two lines. Text written without spaces breaks between characters."""
    words = title.split()
    if len(words) != 1 or _title_width(title) <= max_chars or _title_width(title) == len(title):
        return _balance_title_lines(words, max_chars)
    middle = min(
        range(1, len(title)),
        key=lambda i: max(_title_width(title[:i]), _title_width(title[i:])),
    )
    return [title[:middle], title[middle:]]


def _balance_title_lines(words: List[str], max_chars: int) -> List[str]:
    """Split title words into one line, or two lines balanced around the middle."""
    text = " ".join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best_lines = [text]
    best_longest = len(text)
    for i in range(1, len(words)):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        longest = max(len(first), len(second))
        if longest < best_longest:
            best_longest = longest
            best_lines = [first, second]
    return best_lines


# ---------------------------------------------------------------------------
# Hook title + point labels + word-level captions (verbatim from SupoClip
# captions.py, except build_assemblyai_ass_subtitles' transcript loading —
# see its docstring)
# ---------------------------------------------------------------------------

# How long a recap's point stays on screen, and the least worth showing.
POINT_LABEL_SECONDS = 3.0
POINT_LABEL_MIN_SECONDS = 1.2


def build_point_label_ass(
    labels: List[Dict[str, Any]],
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    position_y: Optional[float] = None,
    hook_end: float = 0.0,
) -> Tuple[str, List[str]]:
    """Text naming each point of a recap, on a dark backing, for about 3 s as the point starts.

    It sits where the hook title sits, the place the layouts keep clear of the
    captions and faces, so a label that would meet the title waits for it to go.
    ``labels`` are {"text", "start"} on the clip timeline, in play order.
    """
    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    base_px = min(64, max(34, int(round(caption_font_px * 0.8))))
    usable_width = video_width - 2 * max(48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC))
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))
    backing_px = max(6, base_px // 4)

    style_line = (
        f"Style: Point,{font_name},{base_px},{primary},&H000000FF,&H59000000,&H59000000,"
        f"1,0,0,0,100,100,0,0,3,{backing_px},0,8,60,60,{margin_v},1"
    )
    events: List[str] = []
    starts = [max(0.0, float(label["start"])) for label in labels]
    for position, label in enumerate(labels):
        raw = " ".join(str(label.get("text") or "").split())
        if not raw:
            continue
        text_value = raw.upper() if template.get("uppercase") else raw
        start = max(starts[position], hook_end)
        end = min(
            start + POINT_LABEL_SECONDS,
            output_duration,
            starts[position + 1] - 0.05 if position + 1 < len(labels) else output_duration,
        )
        if end - start < POINT_LABEL_MIN_SECONDS:
            continue
        max_chars = max(10, int(usable_width / (base_px * 0.52)))
        lines = _title_lines(text_value, max_chars)
        longest = max(_title_width(line) for line in lines)
        size = f"\\fs{max(24, int(usable_width / (longest * 0.52)))}" if longest > max_chars else ""
        placement = f"\\an2\\pos({video_width // 2},{round(video_height * position_y)})" if position_y is not None else ""
        body = "\\N".join(escape_ass_text(line) for line in lines)
        events.append(
            f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Point,,0,0,0,,"
            f"{{\\fad(120,160){placement}{size}}}{body}"
        )
    return style_line, events


def build_hook_title_ass(
    hook_title: str,
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    position_y: Optional[float] = None,
    editor_style: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for a burned-in hook title.

    The title sits in the top safe area (Alignment 8), styled off the caption
    template so it reads as part of the same design system: same font, an
    outline/backing for contrast, power words and numbers in the template's
    highlight colour, and a quick fade+pop entrance.
    """
    uppercase = bool(template.get("uppercase"))
    title_text = hook_title.upper() if uppercase else hook_title

    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    highlight = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")

    # Larger than the captions: the headline is what a scrolling viewer reads
    # before the spoken line. Long titles wrap to two lines, and a line that
    # still does not fit (a long compound word, or two long lines) shrinks
    # below the preferred size so it is not clipped at the frame edge.
    base_px = min(96, max(caption_font_px + 8, int(round(caption_font_px * 1.35))))
    if editor_style is not None:
        base_px = int(editor_style["base_px"])
    usable_width = video_width - 2 * max(48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC))
    max_chars = max(10, int(usable_width / (base_px * 0.52)))
    lines = _title_lines(title_text, max_chars)
    longest = max(_title_width(line) for line in lines)
    hook_px = base_px
    if longest > max_chars:
        fitted = int(usable_width / (longest * 0.52))
        hook_px = min(base_px, max(1, fitted))
        if editor_style is not None:
            hook_px = max(30, hook_px)
    original_px = hook_px
    if editor_style is not None:
        hook_px *= float(editor_style.get("scale", 1))

    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None and base_stroke > 0
    border_style = 3 if (not has_outline and template.get("background_color")) else 1
    outline_px = (
        max(base_stroke, round(hook_px * base_stroke / 26)) if has_outline else 0
    )
    if border_style == 3:
        outline_px = max(4, hook_px // 6)  # backing-box padding
    elif outline_px == 0:
        outline_px = max(2, hook_px // 16)  # always keep contrast on video
    shadow_px = max(2, hook_px // 20) if template.get("shadow") else 0
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))

    style_line = (
        f"Style: Hook,{font_name},{hook_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},8,60,60,{margin_v},1"
    )

    # Accent power words / numbers in the template highlight colour.
    rendered_lines: List[str] = []
    for line in lines:
        spans: List[str] = []
        for word in line.split():
            token = normalize_token(word)
            accented = bool(token) and (token in POWER_WORDS or any(c.isdigit() for c in token))
            color = highlight if accented else primary
            spans.append(f"{{\\c{color}}}{escape_ass_text(word)}")
        rendered_lines.append(" ".join(spans))
    text = "\\N".join(rendered_lines)

    start = 0.12
    end = min(HOOK_TITLE_SECONDS, max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25))
    if output_duration <= HOOK_TITLE_MIN_SECONDS:
        start, end = 0.0, max(0.5, output_duration)
    if editor_style is not None and "start" in editor_style:
        start = max(0.0, min(float(editor_style["start"]), max(0.0, output_duration - .04)))
        end = max(start + min(.04, output_duration), min(float(editor_style["end"]), output_duration))
    span = max(0.001, end - start)
    fade_in, fade_out = min(.16, span / 3), min(.24, span / 3)
    entrance = f"\\fad({round(fade_in * 1000)},{round(fade_out * 1000)})"
    if editor_style is not None:
        x = video_width * (0.5 + float(editor_style.get("x", 0)))
        y = margin_v + original_px * 1.2 * len(lines) / 2 + video_height * float(editor_style.get("y", 0))
        entrance += f"\\an5\\pos({round(x)},{round(y)})"
    elif position_y is not None:
        entrance += f"\\an2\\pos({video_width // 2},{round(video_height * position_y)})"
    if template.get("word_pop", True):
        entrance += f"\\fscx90\\fscy90\\t(0,{round(fade_in * 1000)},\\fscx100\\fscy100)"
    events = [
        f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Hook,,0,0,0,,"
        f"{{{entrance}}}{text}"
    ]
    return style_line, events


def _normalize_words(words):
    """Clip-relative words in SupoClip shape: {"text", "start", "end"}.

    Clipfac transcript words use the ``word`` key; SupoClip uses ``text``.
    Both are accepted; anything without a usable time range is dropped.
    """
    out = []
    for w in words or []:
        text = str(w.get("text", w.get("word", "")) or "").strip()
        try:
            start = float(w.get("start", 0))
            end = float(w.get("end", 0))
        except (TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        out.append({"text": text, "start": start, "end": end})
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def _chunk_bounds(word_count: int, chunk_size: int):
    """Caption lines of ``chunk_size`` words."""
    start = 0
    while start < word_count:
        stop = min(word_count, start + chunk_size)
        yield start, stop
        start = stop


def build_assemblyai_ass_subtitles(
    video_width: int,
    video_height: int,
    output_ass_path: Path,
    words: Optional[List[Dict[str, Any]]] = None,
    clip_duration: Optional[float] = None,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    hook_title: Optional[str] = None,
    include_captions: bool = True,
    position_y_override: Optional[float] = None,
    highlight_words: Optional[List[str]] = None,
    template_override: Optional[Dict[str, Any]] = None,
    font_px_override: Optional[int] = None,
    center_y_override: Optional[int] = None,
    emphasis_indices: Optional[Iterable[int]] = None,
    wrap_lines: bool = False,
    center_x_override: Optional[int] = None,
    hook_editor_style: Optional[Dict[str, Any]] = None,
    caption_placements: Optional[List[Optional[Dict[str, Any]]]] = None,
    avoid_band: Optional[Tuple[float, float]] = None,
    caption_layout_report: Optional[Dict[str, Any]] = None,
    point_labels: Optional[List[Dict[str, Any]]] = None,
    caption_cues: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Generate animated word-synced ASS subtitles from clip-relative words.

    ADAPTED from SupoClip: the original loaded a cached AssemblyAI transcript
    off ``video_path`` (and ``keep_ranges`` / ``output_offsets`` for multi-range
    recuts). Clipfac already owns the word timing — the caller passes the
    clip-relative words (``recut.virtual_transcript`` for a recut clip) — so
    this takes ``words`` + ``clip_duration`` directly. Everything else is
    verbatim: per-word karaoke highlight that pops, accent colour on
    emphasised power/keyword words, contextual emojis (off in Clipfac — no
    colour-emoji font), a thick scaled outline + drop shadow, and an optional
    pill behind the active word. When ``hook_title`` is set, a headline is
    burned into the top safe area while the hook plays out (it renders even
    when word-synced captions are unavailable or disabled).

    The clip editor holds final style values itself: ``template_override``
    replaces the named template, ``font_px_override`` skips width scaling,
    ``center_y_override`` places the caption centre without the safe-area
    clamp, ``emphasis_indices`` replaces automatic emphasis with exactly those
    words, and ``wrap_lines`` lets libass wrap lines wider than the frame.
    ``avoid_band`` is a (top, bottom) height fraction holding captions burned
    into the source; the spoken captions move clear of it.
    ``point_labels`` are a recap's on-screen points (``build_point_label_ass``).
    """
    template = template_override or get_template(caption_template)
    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    animation = template.get("animation", "karaoke")

    relevant_words: List[Dict[str, Any]] = (
        _normalize_words(words) if include_captions else []
    )
    if not relevant_words and not hook_title and not point_labels:
        logger.warning("No words or hook title available for ASS subtitles")
        return False

    effective_font_family, template = ensure_glyph_coverage(
        effective_font_family,
        template,
        [hook_title or ""]
        + [str(label.get("text", "")) for label in point_labels or []]
        + [str(word.get("text", "")) for word in relevant_words],
    )

    # --- styling knobs (new template fields, all optional) ---
    uppercase = bool(template.get("uppercase"))
    # Only inject emojis when the runtime can actually render them in colour.
    enable_emoji = bool(template.get("emoji", True)) and emoji_rendering_supported()
    word_pop = bool(template.get("word_pop", True))
    word_box = bool(template.get("word_box"))
    glow = bool(template.get("glow"))
    has_outline = template.get("stroke_color") is not None
    # Emphasis colouring only makes sense when something distinguishes words.
    enable_emphasis = (
        emphasis_indices is not None or animation != "none" or bool(highlight_words)
    )

    primary = hex_to_ass_color(effective_font_color)
    highlight = hex_to_ass_color(template.get("highlight_color"), "#FFE000")
    emphasis_color = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")
    box_color = hex_to_ass_color(
        template.get("word_box_color") or template.get("highlight_color"), "#00BF49"
    )

    font_px = font_px_override or get_scaled_font_size(effective_font_size, video_width)
    base_stroke = int(template.get("stroke_width", 3) or 0)
    # Scale the outline with the font so big captions keep a chunky, readable edge.
    outline_px = (
        max(base_stroke, round(font_px * base_stroke / 26))
        if (has_outline and base_stroke)
        else 0
    )
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    box_bord = max(outline_px + 2, font_px // 5)
    pos_y = (
        float(position_y_override)
        if position_y_override is not None
        else float(template.get("position_y", 0.80))
    )
    est_text_height = int(font_px * 1.5)
    y_pos = (
        int(center_y_override)
        if center_y_override is not None
        else get_safe_vertical_position(video_height, est_text_height, pos_y)
    )
    if avoid_band is not None and center_y_override is None:
        y_pos = caption_center_clear_of_band(y_pos, est_text_height, avoid_band, video_height)
    if caption_layout_report is not None:
        caption_layout_report["caption_y"] = y_pos / video_height
    font_name = ass_font_name(effective_font_family)
    border_style = (
        3
        if template.get("background") and template.get("background_color")
        else 1
    )
    # libass fills BorderStyle 3 boxes with the outline colour and pads them by
    # the outline width, so an outline-less template would get no box at all.
    style_outline, style_bord = outline, outline_px
    if border_style == 3:
        style_outline, style_bord = back_color, max(outline_px, font_px // 6)

    output_duration = float(clip_duration) if clip_duration else (
        max([float(w["end"]) for w in relevant_words], default=0.0)
    )
    hook_style_block = ""
    hook_events: List[str] = []
    if hook_title:
        hook_style_line, hook_events = build_hook_title_ass(
            hook_title,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
            # An upper-third caption seam belongs to a reaction stack. Keep
            # the headline below the source subject, off the reactor's face.
            position_y=0.94 if position_y_override is not None and position_y_override < .4 else None,
            editor_style=hook_editor_style,
        )
        hook_style_block = f"{hook_style_line}\n"
    if point_labels:
        point_style_line, point_events = build_point_label_ass(
            point_labels,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
            position_y=0.94 if position_y_override is not None and position_y_override < .4 else None,
            hook_end=min(HOOK_TITLE_SECONDS, output_duration) + 0.1 if hook_title else 0.0,
        )
        hook_style_block += f"{point_style_line}\n"
        hook_events = hook_events + point_events

    # Contextual emoji + emphasis annotations over the whole clip word list.
    emoji_by_idx, emphasis_idx = annotate_caption_words(
        relevant_words,
        caption_cues,
        enable_emoji=enable_emoji,
        enable_emphasis=enable_emphasis and emphasis_indices is None,
    )
    if emphasis_indices is not None:
        emphasis_idx = set(emphasis_indices)
    requested_highlights = {
        normalize_token(word)
        for word in (highlight_words or [])
        if normalize_token(word)
    }
    emphasis_idx.update(
        index
        for index, word in enumerate(relevant_words)
        if normalize_token(str(word.get("text", ""))) in requested_highlights
    )

    max_words = max(1, int(template.get("max_words_per_line", 4) or 4))
    chunk_size = max_words

    # Word-count chunks can be far wider than the frame ("5,000 MILLIAMP-HOUR
    # RECHARGEABLE BATTERY"). Measure each chunk as libass will draw it, split
    # the ones that overflow the side margins, and shrink a single word that
    # still does not fit. The editor lets libass wrap instead (``wrap_lines``).
    measure = caption_text_measurer(effective_font_family, font_px)
    space_width = measure(" ")
    edge_px = max(style_bord, box_bord if word_box else 0) + shadow_px
    peak_scale = {"pop": 1.06, "bounce": 1.12}.get(animation, 1.0)
    available_width = (get_subtitle_max_width(video_width) - 2 * edge_px) / peak_scale
    emoji_width = space_width + font_px * 1.2

    def display_width(index: int, word: Dict[str, Any]) -> float:
        text = str(word.get("text", ""))
        width = measure(text.upper() if uppercase else text)
        return width + (emoji_width if emoji_by_idx.get(index) else 0.0)

    word_widths = [display_width(index, word) for index, word in enumerate(relevant_words)]
    chunk_spans: List[Tuple[int, int, float]] = []
    for chunk_start, chunk_stop in _chunk_bounds(len(relevant_words), chunk_size):
        if wrap_lines:
            chunk_spans.append((chunk_start, chunk_stop, 1.0))
            continue
        widths = word_widths[chunk_start:chunk_stop]
        for start, end in split_caption_chunk(widths, space_width, available_width):
            line_width = sum(widths[start:end]) + space_width * (end - start - 1)
            scale = min(1.0, available_width / line_width) if line_width > 0 else 1.0
            chunk_spans.append((chunk_start + start, chunk_start + end, scale))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: {0 if wrap_lines else 2}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_px},{primary},&H000000FF,{style_outline},{back_color},1,0,0,0,100,100,0,0,{border_style},{style_bord},{shadow_px},5,60,60,60,1
{hook_style_block}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    x_pos = video_width // 2 if center_x_override is None else center_x_override

    def scaled(percent: int, scale: float) -> int:
        return max(1, round(percent * scale))

    def line_prefix(scale: float) -> str:
        tags = f"\\pos({x_pos},{y_pos})" + ("\\blur4" if glow else "")
        if scale < 1.0:
            tags += f"\\fscx{scaled(100, scale)}\\fscy{scaled(100, scale)}"
        return f"{{{tags}}}"

    # Every word span re-declares the caption font, so an emoji's \fn override
    # can never leak into the following word.
    font_tag = f"\\fn{font_name}"

    def render_text(global_idx: int, word: Dict[str, Any]) -> str:
        text = str(word.get("text", ""))
        if uppercase:
            text = text.upper()
        disp = escape_ass_text(text)
        emoji = emoji_by_idx.get(global_idx)
        if emoji:
            # Force the colour-emoji font for the glyph; the next word's span
            # re-declares the caption font, so no explicit restore is needed.
            disp = f"{disp} {{\\fn{EMOJI_FONT_NAME}}}{emoji}"
        return disp

    # The active word is distinguished by COLOUR only (and an optional box). We
    # deliberately do NOT scale individual words: scaling a word changes its
    # advance width, which reflows the centre-anchored line and makes the whole
    # caption visibly vibrate as each word pops. The "pop" lives as a one-shot
    # line entrance instead (see below).
    def active_span(disp: str) -> str:
        tags = f"{font_tag}\\c{highlight}"
        if word_box:
            tags += f"\\3c{box_color}\\bord{box_bord}\\shad0"
        return f"{{{tags}}}{disp}"

    def idle_span(global_idx: int, disp: str) -> str:
        color = emphasis_color if (enable_emphasis and global_idx in emphasis_idx) else primary
        tags = f"{font_tag}\\c{color}"
        if word_box:
            tags += f"\\3c{outline}\\bord{outline_px}\\shad{shadow_px}"
        return f"{{{tags}}}{disp}"

    # Subtle one-shot entrance for the whole line (uniform scale, centred), shown
    # only as the first word of a chunk appears — gives a pop without any
    # per-word reflow/vibration.
    def line_entrance(scale: float) -> str:
        if not word_pop:
            return ""
        small, full = scaled(92, scale), scaled(100, scale)
        return f"\\fscx{small}\\fscy{small}\\t(0,140,\\fscx{full}\\fscy{full})"

    events: List[str] = []
    original_geometry = (outline_px, shadow_px, box_bord)
    for chunk_start, chunk_stop, chunk_scale in chunk_spans:
        chunk = relevant_words[chunk_start:chunk_stop]
        indices = list(range(chunk_start, chunk_stop))
        chunk_end = float(chunk[-1]["end"])
        prefix = line_prefix(chunk_scale)
        entrance_tags = line_entrance(chunk_scale)
        outline_px, shadow_px, box_bord = original_geometry
        placement = (caption_placements[chunk_start // chunk_size]
                     if caption_placements and chunk_start // chunk_size < len(caption_placements) else None)
        if placement is not None:
            px = placement["font_px"]
            outline_px = max(base_stroke, round(px * base_stroke / 26)) if has_outline else 0
            shadow_px = max(2, px // 20) if template.get("shadow") else 0
            box_bord = max(outline_px + 2, px // 5)
            border = max(outline_px, px // 6) if border_style == 3 else outline_px
            prefix = f"{{\\pos({placement['x']},{placement['y']})\\fs{px}\\bord{border}\\shad{shadow_px}" + ("\\blur4" if glow else "") + "}"

        if animation == "karaoke":
            for local_i, word in enumerate(chunk):
                start = float(word["start"])
                end = (
                    float(chunk[local_i + 1]["start"])
                    if local_i + 1 < len(chunk)
                    else chunk_end
                )
                if end <= start:
                    end = start + 0.05
                parts = []
                for local_j, other in enumerate(chunk):
                    gj = indices[local_j]
                    disp = render_text(gj, other)
                    parts.append(active_span(disp) if local_j == local_i else idle_span(gj, disp))
                line = " ".join(parts)
                # Entrance only on the first word's event so it plays once, not
                # once per word.
                entrance = f"{{{entrance_tags}}}" if (entrance_tags and local_i == 0) else ""
                events.append(
                    f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{entrance}{line}"
                )
        else:
            start = float(chunk[0]["start"])
            end = chunk_end
            if end <= start:
                end = start + 0.05
            spans = []
            for local_j, word in enumerate(chunk):
                gj = indices[local_j]
                disp = render_text(gj, word)
                color = emphasis_color if (enable_emphasis and gj in emphasis_idx) else primary
                spans.append(f"{{{font_tag}\\c{color}}}{disp}")
            chunk_text = " ".join(spans)

            effect = ""
            # (from %, peak %, ms to peak, ms to settle) of the scale animation
            scale_steps = {"pop": (88, 106, 130, 250), "bounce": (70, 112, 120, 240)}
            if animation == "fade":
                effect = "{\\fad(120,120)}"
            elif animation in scale_steps:
                low, peak, rise, settle = scale_steps[animation]
                low, peak, full = (scaled(p, chunk_scale) for p in (low, peak, 100))
                effect = (
                    f"{{\\fscx{low}\\fscy{low}\\t(0,{rise},\\fscx{peak}\\fscy{peak})"
                    f"\\t({rise},{settle},\\fscx{full}\\fscy{full})}}"
                )
            events.append(
                f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{effect}{chunk_text}"
            )

    all_events = hook_events + events
    output_ass_path.write_text(header + "\n".join(all_events) + "\n", encoding="utf-8")
    logger.info(
        "Wrote ASS subtitles: %s (%d events%s)",
        output_ass_path,
        len(all_events),
        ", hook title" if hook_events else "",
    )
    return True
