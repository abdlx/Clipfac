// Plain-node test for editorUtils.js (no Vitest in this dashboard).
// Run: node --test dashboard/src/components/editorUtils.test.mjs
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
    totalOf, round3, segmentsEqual, mergeSegments, splitSegments, clipToSource,
} from './editorUtils.js';

const segs = [
    { start: 10, end: 20 },
    { start: 30, end: 45 },
    { start: 50, end: 55 },
];

describe('mergeSegments', () => {
    it('merges a segment with the next one, spanning both', () => {
        assert.deepEqual(mergeSegments(segs, 0), [
            { start: 10, end: 45 },
            { start: 50, end: 55 },
        ]);
    });

    it('merges the middle pair and keeps the rest in order', () => {
        assert.deepEqual(mergeSegments(segs, 1), [
            { start: 10, end: 20 },
            { start: 30, end: 55 },
        ]);
    });

    it('returns null for the last segment and for bad input', () => {
        assert.equal(mergeSegments(segs, 2), null);
        assert.equal(mergeSegments(segs, -1), null);
        assert.equal(mergeSegments(segs, 3), null);
        assert.equal(mergeSegments([], 0), null);
        assert.equal(mergeSegments(null, 0), null);
    });

    it('does not mutate the input list', () => {
        const before = JSON.stringify(segs);
        mergeSegments(segs, 0);
        assert.equal(JSON.stringify(segs), before);
    });

    it('merge spans the source gap between the two segments', () => {
        // Segments 2 and 3 sit 5s apart in the source (45..50); the merged
        // segment is one continuous source range, so it includes that gap.
        assert.equal(totalOf(mergeSegments(segs, 1)), totalOf(segs) + 5);
    });

    it('merge of contiguous segments keeps the total', () => {
        const contiguous = [{ start: 10, end: 20 }, { start: 20, end: 35 }];
        assert.equal(totalOf(mergeSegments(contiguous, 0)), totalOf(contiguous));
    });
});

describe('splitSegments', () => {
    it('splits at the given source time with 3-decimal rounding', () => {
        assert.deepEqual(splitSegments(segs, 0, 15.1234), [
            { start: 10, end: 15.123 },
            { start: 15.123, end: 20 },
            { start: 30, end: 45 },
            { start: 50, end: 55 },
        ]);
    });

    it('refuses a split that would leave a side under minSeg', () => {
        assert.equal(splitSegments(segs, 2, 50.2), null);
        assert.equal(splitSegments(segs, 2, 54.8), null);
        assert.equal(splitSegments(segs, 0, 10), null);
        assert.equal(splitSegments(segs, 0, 20), null);
    });

    it('split then merge is the identity (round-trip)', () => {
        const split = splitSegments(segs, 1, 40);
        assert.deepEqual(mergeSegments(split, 1), segs);
    });
});

describe('helpers', () => {
    it('totalOf sums segment lengths', () => {
        assert.equal(totalOf(segs), 30);
    });

    it('round3 rounds to milliseconds', () => {
        assert.equal(round3(1.23456), 1.235);
    });

    it('segmentsEqual compares by value', () => {
        assert.equal(segmentsEqual(segs, [...segs]), true);
        assert.equal(segmentsEqual(segs, mergeSegments(segs, 0)), false);
    });

    it('clipToSource walks the assembly in order', () => {
        assert.equal(clipToSource(segs, 0), 10);
        assert.equal(clipToSource(segs, 12), 32);
        assert.equal(clipToSource(segs, 25), 50);
        assert.equal(clipToSource(segs, 999), 55);
    });
});
