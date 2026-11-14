"""Caption templates — direct port of SupoClip's caption_templates.py."""
from clipfac.caption_templates import (
    CAPTION_TEMPLATES, TEMPLATE_DEFAULTS, get_template, get_all_templates,
    get_template_names, get_template_info,
)


class TestTemplateSet:
    def test_all_seven_templates_present(self):
        assert get_template_names() == [
            "default", "hormozi", "mrbeast", "minimal",
            "tiktok", "neon", "podcast",
        ]
        assert len(CAPTION_TEMPLATES) == 7

    def test_each_template_has_the_renderer_fields(self):
        required = {"name", "description", "font_family", "font_size",
                    "font_color", "highlight_color", "stroke_color",
                    "stroke_width", "animation", "max_words_per_line",
                    "position_y"}
        for name, template in CAPTION_TEMPLATES.items():
            assert required <= set(template), name

    def test_template_ids_match_supoclip_fonts(self):
        # THEBOLDFONT / TikTokSans were copied into Clipfac fonts/ with these.
        assert CAPTION_TEMPLATES["hormozi"]["font_family"] == "THEBOLDFONT"
        assert CAPTION_TEMPLATES["minimal"]["font_family"] == "TikTokSans-Regular"
        assert CAPTION_TEMPLATES["tiktok"]["font_family"] == "TikTokSans-Bold"


class TestGetTemplate:
    def test_unknown_name_falls_back_to_default(self):
        assert get_template("nope")["name"] == "Default"
        assert get_template("")["name"] == "Default"

    def test_defaults_fill_missing_keys(self):
        # A template omitting an optional key still renders: the merge with
        # TEMPLATE_DEFAULTS supplies it.
        merged = get_template("minimal")
        assert merged["word_pop"] is False  # template's own value wins
        assert merged["animation"] == "fade"
        assert merged["max_words_per_line"] == 6

    def test_get_template_does_not_mutate_the_store(self):
        merged = get_template("hormozi")
        merged["font_size"] = 999
        assert CAPTION_TEMPLATES["hormozi"]["font_size"] == 32

    def test_hormozi_is_the_word_box_template(self):
        t = get_template("hormozi")
        assert t["word_box"] is True
        assert t["word_box_color"] == "#00BF49"
        assert t["highlight_color"] == "#00FF66"
        assert t["uppercase"] is True


class TestTemplateInfo:
    def test_info_lists_all_templates_for_the_picker(self):
        info = get_template_info()
        assert [t["id"] for t in info] == get_template_names()
        assert info[1] == {
            "id": "hormozi", "name": "Hormozi",
            "description": CAPTION_TEMPLATES["hormozi"]["description"],
            "animation": "karaoke", "font_family": "THEBOLDFONT",
            "font_size": 32, "font_color": "#FFFFFF",
            "highlight_color": "#00FF66",
        }

    def test_get_all_templates_returns_the_store(self):
        assert get_all_templates() is CAPTION_TEMPLATES
        assert set(TEMPLATE_DEFAULTS) <= set(get_template("default"))
