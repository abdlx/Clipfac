"""Versioned editorial looks shared by REST, MCP and the rendering subprocess."""
from copy import deepcopy

PRESETS = {
    "podcast": {
        "name": "Editorial", "theme": "podcast", "description": "Stable speaker framing, sentence-case captions and restrained emphasis.",
        "accent": "#82B2FF", "caption_preset": "clean", "hook_style": "pill",
        "layouts": ["speaker_cut"], "hook": True,
        "caption": {"font_name": "Montserrat ExtraBold", "font_size": 38, "font_color": "#FFFFFF", "highlight_color": "#82B2FF", "border_width": 2, "shadow": 2, "effect": "none", "uppercase": False, "max_chars": 11},
        "direction": "Favor complete exchanges and clear payoffs. Avoid gratuitous zooms and misleading hooks.",
    },
    "gaming": {
        "name": "Playmaker", "theme": "gaming", "description": "Stable HUD overview and enlarged centre gameplay, with no avatar tracking.",
        "accent": "#A3FF12", "caption_preset": "lime", "hook_style": "dark",
        "layouts": [], "hook": True,
        "caption": {"font_name": "Montserrat ExtraBold", "font_size": 30, "highlight_color": "#A3FF12", "effect": "highlight", "border_width": 3, "uppercase": True, "max_chars": 10},
        "direction": "Preserve gameplay context and interface visibility. Select complete reactions and event payoffs.",
    },
    "education": {
        "name": "Clarity", "theme": "education", "description": "Readable demonstrations, calm typography and minimal decoration.",
        "accent": "#67E8F9", "caption_preset": "clean", "hook_style": "pill",
        "layouts": ["screencast"], "hook": True,
        "caption": {"font_name": "Montserrat ExtraBold", "font_size": 36, "highlight_color": "#67E8F9", "effect": "none", "border_width": 2, "shadow": 2, "uppercase": False, "max_chars": 12},
        "direction": "Keep each explanation self-contained. Preserve qualifications and any demonstration needed to understand it.",
    },
    "fitness": {
        "name": "Momentum", "theme": "fitness", "description": "Bold emphasis and movement-aware subject tracking.",
        "accent": "#FFB86B", "caption_preset": "hormozi", "hook_style": "outline",
        "layouts": ["punch_in"], "hook": True,
        "caption": {"font_name": "Anton", "font_size": 48, "highlight_color": "#FFB86B", "effect": "pop", "border_width": 4, "uppercase": True, "max_chars": 14},
        "direction": "Keep exercise form visible. Favor meaningful transformations, techniques and clear outcomes; preserve safety context.",
    },
    "cinematic": {
        "name": "Frame", "theme": "cinematic", "description": "Quiet captions, faithful composition and no automatic headline.",
        "accent": "#F5DCA9", "caption_preset": "clean", "hook_style": "classic",
        "layouts": [], "hook": False,
        "caption": {"font_name": "Montserrat ExtraBold", "font_size": 34, "highlight_color": "#FFFFFF", "effect": "none", "border_width": 1, "shadow": 2, "uppercase": False, "max_chars": 13},
        "direction": "Preserve the scene's emotional rhythm. Avoid abrupt truncation and redundant overlays.",
    },
    "campaign": {
        "name": "Campaign", "theme": "campaign", "description": "Controlled product emphasis with a campaign-specific editorial brief.",
        "accent": "#C4B5FD", "caption_preset": "hormozi", "hook_style": "pill",
        "layouts": ["auto"], "hook": True,
        "caption": {"font_name": "Montserrat ExtraBold", "font_size": 40, "highlight_color": "#C4B5FD", "effect": "pop", "border_width": 3, "uppercase": False, "max_chars": 10},
        "direction": "Prioritize approved product demonstrations and claims. Do not invent testimonials, benefits, offers or endorsements.",
    },
}


def get_preset(preset_id):
    if preset_id not in PRESETS:
        raise ValueError(f"Unknown style: {preset_id}")
    return {"id": preset_id, "version": 2 if preset_id == "gaming" else 1, **deepcopy(PRESETS[preset_id])}


def list_presets():
    return [get_preset(key) for key in PRESETS]


def render_caption_style(base, preset_id):
    """Return a fresh style; never mutate the cross-job default."""
    style = dict(base)
    if preset_id:
        style.update(get_preset(preset_id)["caption"])
        style["base_opacity"] = 1.0
    return style
