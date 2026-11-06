# Moment quality and gaming composition

Clip counts are ceilings. Both speech and silent-video selection may return zero;
there is no minimum-count retry. A completed analysis can contain no candidates.

Clipfac analyses review each candidate before creating plans. Direct Clipfac
productions perform the same review before encoding. Each candidate gets up to
six timestamped source stills, plus timestamped transcript context extending five
seconds outside the proposed cut. Context is explicitly excluded from the clip.
The review uses the configured director vision provider (Gemini or an
OpenAI-compatible vision endpoint). Maximum 60 candidate reviews, each subject
to the existing provider timeout. Additional provider charges are recorded as
unknown unless prices are configured; they are not treated as free.

The gate holds missing/failed vision, uncertain decisions, confidence below .75,
missing setup/payoff, incomplete boundaries, and dominant dead time. These are
model judgments, not measured accuracy or guaranteed performance. Stills do not
verify continuous action, fine action timing, audio quality, or every caption.
A strong self-contained spoken story may legitimately accompany driving.
Inventory, maps or travel alone do not establish a highlight.

Word timestamps repair nearby sentence/pause boundaries by expansion, within
five seconds per edge and within the requested duration band. When completing
the ending does not fit, the candidate is held instead of losing its payoff.
Missing word alignment is disclosed by the lack of a boundary correction; the
multimodal reviewer must assess the segment transcript. This gate does not create
jump cuts or remove interior dead time: select another moment or revise the cut.
Manual plan revisions check speech boundaries and suggest valid edges without
silently changing the requested cut. They do not receive a new visual model
review; inspect revised renders with the existing review tools.

## Gaming preset version 2

The automatic vertical gaming route bypasses face/avatar detectors and per-scene
screencast routing. Landscape footage gets a full-width overview preserving the
complete source/HUD above a larger fixed centre detail. Remaining space is a
neutral background. Portrait footage is fitted without duplicate views. No
blurred duplicate and no inventory-face zoom. The centre crop is not enemy or
crosshair tracking: action away from the centre still requires manual framing.
Horizontal, square and explicit framing overrides retain their chosen routes.

Gaming captions are reduced from size 40 to 30. The new preset version invalidates
old pinned gaming plans until they are revised, avoiding silent visual changes.
No model call is made while rendering an immutable plan.

## Verification

Run `python -m unittest discover -s tests -p test_editorial_regressions.py -v`.
These offline checks exercise real FFmpeg encoding/audio/layout metadata,
sentence expansion and refusal, missing-vision holds, score-gate integration,
manual renderer overrides, and the no-quota policy. Full pytest and model-backed
quality evaluation require the project's test dependencies and configured keys.
The uploaded clips are already rendered exports; they cannot establish new
source selection quality without the original source and transcript.
