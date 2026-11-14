// Pure editor logic for ClipEditor.jsx — kept dependency-free so it can be
// unit-tested with plain node (no Vitest in this dashboard) and reused by
// any future timeline view. Every function is total: invalid input returns
// null instead of throwing, so the editor can disable the button instead of
// crashing. Segments are {start, end} in source seconds; order is the clip
// order, and all values round-trip through 3 decimals like the EDL.

export function totalOf(segments) {
    return segments.reduce((acc, s) => acc + (s.end - s.start), 0);
}

export function round3(t) {
    return Math.round(t * 1000) / 1000;
}

export function segmentsEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}

// Merge segment `index` with the one after it: one segment spanning both.
// Returns the new list, or null when there is nothing after `index` to merge
// with. The merged segment keeps the first segment's start and the second's
// end, so a merge is exactly expressible as an EDL edit and re-renders
// through the existing POST /api/clip/rerender path — no backend needed.
export function mergeSegments(segments, index) {
    if (!Array.isArray(segments) || index < 0 || index >= segments.length - 1) return null;
    const next = segments.slice();
    next.splice(index, 2, {
        start: segments[index].start,
        end: segments[index + 1].end,
    });
    return next;
}

// Split segment `index` at source time `at` (must leave >= minSeg on both
// sides). Returns the new list, or null when the split point is unusable.
export function splitSegments(segments, index, at, minSeg = 0.5) {
    if (!Array.isArray(segments) || index < 0 || index >= segments.length) return null;
    const seg = segments[index];
    if (typeof at !== 'number' || !Number.isFinite(at)) return null;
    if (at - seg.start < minSeg || seg.end - at < minSeg) return null;
    const next = segments.flatMap((s, i) => (i === index
        ? [{ start: s.start, end: round3(at) }, { start: round3(at), end: s.end }]
        : [s]));
    return next;
}

// Clip-relative time -> source time, walking the segment list (the inverse
// of the backend's rebase_segments). Used by the playhead/seek logic.
export function clipToSource(segments, t) {
    let acc = 0;
    for (const seg of segments) {
        const len = seg.end - seg.start;
        if (t < acc + len) return seg.start + (t - acc);
        acc += len;
    }
    const last = segments[segments.length - 1];
    return last ? last.end : 0;
}
