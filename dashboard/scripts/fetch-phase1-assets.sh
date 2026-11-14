#!/bin/sh
# fetch-phase1-assets.sh — fetch the Phase 1 binary assets that cannot be
# committed through the text-only deploy tooling (push-files carries UTF-8
# text only, so .onnx/.ttf files are rejected).
#
# Usage (run from the build context root):
#   sh scripts/fetch-phase1-assets.sh backend    # API image: YuNet model +
#                                                 # 3 caption fonts -> fonts/
#   sh scripts/fetch-phase1-assets.sh frontend  # dashboard build: 3 caption
#                                                 # fonts -> public/fonts/
#   (run from the dashboard directory for frontend mode)
#
# Local-file-first: a file that already exists with the expected SHA-256 is
# left alone. Otherwise the file is downloaded from its public source and
# the SHA-256 is verified; any mismatch or download failure aborts
# (set -e), so a build never silently ships without the assets.
#
# Sources (all verified byte-identical to the Phase 1 files, 2026-11-14):
#   YuNet ONNX  — OpenCV model zoo (Git LFS media endpoint; the raw URL
#                 serves only the 131-byte LFS pointer):
#                 media.githubusercontent.com/media/opencv/opencv_zoo
#   All 3 fonts — SupoClip's public repo (the fonts were lifted from there;
#                 THEBOLDFONT is Sven Pels' free font, TikTok Sans is OFL):
#                 raw.githubusercontent.com/FujiwaraChoki/supoclip
#
# NOTE: this file is duplicated at dashboard/scripts/fetch-phase1-assets.sh
# (the dashboard build context is the dashboard/ directory, so it cannot
# see the repo-root copy). Keep the two copies in sync.
set -eu

MODE="${1:-backend}"

YUNET_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_SHA="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SUPO_BASE="https://raw.githubusercontent.com/FujiwaraChoki/supoclip/main/backend/fonts"
BOLD_SHA="5751022bdfe00903cf6ec5d4a8d23920952ecc213169bbaf19f28d8dedc54339"
TTB_SHA="04c5f110157c1f8a84319d850b6c1ae0ea95798ea2a8b1326262d06e68d00064"
TTR_SHA="340bcf4fc0901573f734b38e64c9bc20b617ca4de614761c38571b7d044eddf3"

download() {
    local url="$1"
    local dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dest" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$dest" "$url"
    else
        echo "fetch-phase1-assets: neither curl nor wget available" >&2
        exit 1
    fi
}

file_sha() {
    sha256sum "$1" | cut -d' ' -f1
}

fetch() {
    local url="$1"
    local dest="$2"
    local sha="$3"
    local tmp
    local actual
    if [ -f "$dest" ] && [ "$(file_sha "$dest")" = "$sha" ]; then
        echo "fetch-phase1-assets: $dest already present, skipping"
        return 0
    fi
    if [ -f "$dest" ]; then
        echo "fetch-phase1-assets: $dest present but SHA mismatch, re-downloading"
    fi
    mkdir -p "$(dirname "$dest")"
    tmp="$dest.download"
    rm -f "$tmp"
    download "$url" "$tmp"
    actual="$(file_sha "$tmp")"
    if [ "$actual" != "$sha" ]; then
        echo "fetch-phase1-assets: SHA-256 mismatch for $dest (got $actual, want $sha)" >&2
        rm -f "$tmp"
        exit 1
    fi
    mv "$tmp" "$dest"
    echo "fetch-phase1-assets: fetched $dest"
}

if [ "$MODE" = "backend" ]; then
    fetch "$YUNET_URL" "clipfac/models/face_detection_yunet_2023mar.onnx" "$YUNET_SHA"
    fetch "$SUPO_BASE/THEBOLDFONT.ttf" "fonts/THEBOLDFONT.ttf" "$BOLD_SHA"
    fetch "$SUPO_BASE/TikTokSans-Bold.ttf" "fonts/TikTokSans-Bold.ttf" "$TTB_SHA"
    fetch "$SUPO_BASE/TikTokSans-Regular.ttf" "fonts/TikTokSans-Regular.ttf" "$TTR_SHA"
elif [ "$MODE" = "frontend" ]; then
    fetch "$SUPO_BASE/THEBOLDFONT.ttf" "public/fonts/THEBOLDFONT.ttf" "$BOLD_SHA"
    fetch "$SUPO_BASE/TikTokSans-Bold.ttf" "public/fonts/TikTokSans-Bold.ttf" "$TTB_SHA"
    fetch "$SUPO_BASE/TikTokSans-Regular.ttf" "public/fonts/TikTokSans-Regular.ttf" "$TTR_SHA"
else
    echo "usage: $0 backend|frontend" >&2
    exit 1
fi

echo "fetch-phase1-assets: all $MODE assets present and verified"
