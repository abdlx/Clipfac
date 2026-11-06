"""Offline regression checks: no external model, downloader or ML dependencies.

Run: python -m unittest discover -s tests -p test_editorial_regressions.py -v
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from clip_selection import clip_count_targets, sentence_aligned_span
from clipfac.editorial import MomentReview, review_candidates, review_reasons
from clipfac.gaming import render, filtergraph
from clipfac.models import ProductionRequest
from clipfac.quality import shortlist


def transcript():
    return {'segments': [{'words': [
        {'word': 'He', 'start': 0, 'end': 1},
        {'word': 'pushed.', 'start': 1, 'end': 2},
        {'word': 'This', 'start': 2.2, 'end': 3},
        {'word': 'fight', 'start': 3, 'end': 4},
        {'word': 'ends.', 'start': 18, 'end': 19},
        {'word': "How's", 'start': 19.2, 'end': 20},
        {'word': 'that', 'start': 20, 'end': 21},
        {'word': 'possible?', 'start': 21, 'end': 22},
    ]}]}


class EditorialRegressions(unittest.TestCase):
    def test_quota_cannot_force_filler(self):
        # A MIN above the MAX cannot force filler beyond the ceiling: the
        # ceiling is the binding constraint, the floor is capped to it.
        with patch.dict(os.environ, {'CLIP_TARGET_MIN': '50', 'CLIP_TARGET_MAX': '4'}):
            self.assertEqual(clip_count_targets(10), (4, 4))

    def test_expands_hows_to_completed_question(self):
        start, end, issues = sentence_aligned_span(2.2, 20, transcript(), 30, 15, 25)
        self.assertEqual(issues, [])
        self.assertGreaterEqual(end, 22)
        self.assertLess(start, 2.2)

    def test_no_room_for_payoff_is_flagged_not_truncated(self):
        _, _, issues = sentence_aligned_span(2.2, 20, transcript(), 30, 15, 18)
        self.assertIn('complete_sentence_outside_duration_band', issues)

    def test_mid_sentence_opening_is_repaired(self):
        start, _, issues = sentence_aligned_span(3, 19.1, transcript(), 30, 15, 30)
        self.assertEqual(issues, [])
        self.assertLess(start, 2.2)

    def test_missing_word_alignment_is_not_fabricated(self):
        self.assertEqual(sentence_aligned_span(2, 20, {}, 30), (2, 20, []))

    def test_weak_inventory_or_travel_is_flagged(self):
        verdict = MomentReview(decision='reject', confidence=.95, setup='Routine driving',
            payoff='', hook='', complete_start=False, complete_end=False,
            dead_time_dominates=True, reason='Inventory and travel without payoff')
        self.assertIn('dead_time_dominates', review_reasons(verdict))
        self.assertIn('missing_setup_or_payoff', review_reasons(verdict))

    def test_model_uncertainty_is_flagged(self):
        verdict = MomentReview(decision='accept', confidence=.5, setup='Setup', payoff='Payoff',
            hook='Observed moment', complete_start=True, complete_end=True,
            dead_time_dominates=False, reason='Limited evidence')
        self.assertIn('editorial_low_confidence', review_reasons(verdict))

    def test_failed_vision_holds_and_preserves_index_order(self):
        clips = [{'start': 0, 'end': 20, 'predicted_score': 99},
                 {'start': 30, 'end': 50, 'predicted_score': 98}]
        with patch('clipfac.editorial.candidate_frames', return_value=([], [])):
            reviewed = review_candidates('unused', {}, 60, clips, 'gaming')
        self.assertEqual([c['start'] for c in reviewed], [0, 30])
        self.assertTrue(all(c['editorial_review']['status'] == 'held' for c in reviewed))
        self.assertNotIn('editorial_review', clips[0])
        request = ProductionRequest(source_url='https://example.com/video', confirm_rights=True)
        self.assertEqual(shortlist(reviewed, request)['accepted'], [])

    def test_valid_moment_passes_with_its_own_frame_evidence(self):
        verdict = MomentReview(decision='accept', confidence=.9, setup='Player is trapped',
            payoff='Escapes the encounter', hook='A narrow escape', complete_start=True,
            complete_end=True, dead_time_dominates=False, reason='Complete visible escape')
        with patch('clipfac.editorial.candidate_frames', return_value=([b'jpg']*6, [0, .8, 5, 10, 15, 19.85])), \
             patch('clipfac.director.generate_json', return_value=(verdict, {'cost_usd': None})):
            result = review_candidates('unused', {}, 30, [{'start': 0, 'end': 20}], 'gaming')[0]
        self.assertEqual(result['editorial_review']['status'], 'passed')
        self.assertEqual(result['viral_hook_text'], 'A narrow escape')
        self.assertEqual(len(result['editorial_review']['sample_timestamps']), 6)

    def test_weak_verdict_is_flagged_but_still_ships(self):
        # Volume first: a reject verdict, low confidence, missing setup/payoff
        # and dead time are warnings, not a hold. The clip ships and the
        # shortlist accepts it; the audience decides what is viral.
        verdict = MomentReview(decision='reject', confidence=.5, setup='',
            payoff='', hook='Routine drive', complete_start=False,
            complete_end=False, dead_time_dominates=True, reason='Weak moment')
        with patch('clipfac.editorial.candidate_frames', return_value=([b'jpg']*6, [0, .8, 5, 10, 15, 19.85])), \
             patch('clipfac.director.generate_json', return_value=(verdict, {'cost_usd': None})):
            result = review_candidates('unused', {}, 30,
                                       [{'start': 0, 'end': 20, 'predicted_score': 85}], 'gaming')[0]
        self.assertEqual(result['editorial_review']['status'], 'passed')
        self.assertIn('editorial_reject', result['editorial_review']['warnings'])
        self.assertIn('editorial_low_confidence', result['editorial_review']['warnings'])
        self.assertIn('dead_time_dominates', result['editorial_review']['warnings'])
        self.assertEqual(result['viral_hook_text'], 'Routine drive')
        request = ProductionRequest(source_url='https://example.com/video', confirm_rights=True)
        self.assertEqual(len(shortlist([result], request)['accepted']), 1)

    def test_provider_outage_stops_repeated_calls(self):
        with patch('clipfac.editorial.candidate_frames', return_value=([b'jpg']*6, [0, 1, 5, 10, 15, 19])), \
             patch('clipfac.director.generate_json', side_effect=RuntimeError('offline')) as provider:
            result = review_candidates('unused', {}, 60,
                                      [{'start': 0, 'end': 20}, {'start': 30, 'end': 50}], 'gaming')
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(all(c['editorial_review']['status'] == 'held' for c in result))

    def test_portrait_gaming_is_not_double_stacked(self):
        graph = filtergraph(1080, 1920)
        self.assertNotIn('vstack', graph)
        self.assertIn('force_original_aspect_ratio=decrease', graph)

    def test_empty_detail_retries_once_then_returns_empty(self):
        # Volume first: an empty detail pass gets ONE second chance over the
        # unused windows (the floor retry). If that also comes back empty,
        # the honest answer is still zero clips — nothing is invented.
        from types import SimpleNamespace
        import clip_selection
        source_tree = ast.parse(Path('main.py').read_text())
        worker_tree = ast.parse(Path('gemini_worker.py').read_text())
        prompts = {}
        for statement in worker_tree.body:
            if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
                if statement.targets[0].id in ('SCORE_PROMPT_TEMPLATE', 'DETAIL_PROMPT_TEMPLATE'):
                    prompts[statement.targets[0].id] = ast.literal_eval(statement.value)
        calls = []
        def stage(*args):
            calls.append(args[-1])
            return [{'id': 'window_001', 'score': 20}] if args[-1] == 'score' else []
        worker = SimpleNamespace(**prompts, ScoreResponse=object, DetailResponse=object,
                                 GeminiBlockedError=RuntimeError)
        ns = {name: getattr(clip_selection, name) for name in (
            'clip_count_targets', 'clip_duration_bounds', 'build_transcript_windows',
            'score_batches', 'shortlist_target', 'trim_to_best', 'snap_clip_to_words', 'dedupe_overlapping')}
        ns.update(os=os, json=json, gemini_worker=worker, _run_stage_split=stage,
            score_batch_size=lambda: 8,
            llm_backend=SimpleNamespace(active=lambda: True, model_name=lambda: 'offline', base_url=lambda: 'local'))
        node = next(n for n in source_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_viral_clips')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), ns)
        result = ns['get_viral_clips']({'segments': [{'start': 0, 'end': 30, 'text': 'Routine travel'}]}, 30)
        self.assertEqual(result['shorts'], [])
        self.assertEqual(calls, ['score', 'detail', 'detail-floor'])

    def test_no_blur_or_face_tracking_in_gaming_composition(self):
        graph = filtergraph(1920, 1080)
        self.assertNotIn('blur', graph)
        self.assertIn('scale=1080:608', graph)
        self.assertIn('crop=1080:1080', graph)
        self.assertIn('pad=1080:1920', graph)

    def test_renderer_route_respects_manual_overrides(self):
        # Parse just the routing function: no torch/YOLO imports needed.
        tree = ast.parse(Path('main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_render_clip')
        ns = {'os': os, 'ASPECT_RATIO': 9/16,
              'process_video_to_vertical': lambda *a, **kw: 'manual',
              'finalize_clip_passthrough': lambda *a: 'horizontal'}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), ns)
        with patch.dict(os.environ, {'CLIPFAC_STYLE': 'gaming'}), patch('clipfac.gaming.render', return_value='gaming'):
            self.assertEqual(ns['_render_clip']('a', 'b', 'vertical', None, None, False), 'gaming')
            self.assertEqual(ns['_render_clip']('a', 'b', 'vertical', 'WIDE', None, False), 'manual')
            self.assertEqual(ns['_render_clip']('a', 'b', 'horizontal', None, None, False), 'horizontal')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_render_preserves_audio_and_stable_layout(self):
        with tempfile.TemporaryDirectory() as root:
            source, output = Path(root)/'source.mp4', Path(root)/'out.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=12', '-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=48000', '-t', '1', '-c:v', 'libx264',
                '-preset', 'ultrafast', '-c:a', 'aac', str(source)], check=True, timeout=30)
            self.assertTrue(render(source, output, 360, 640))
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)],
                                    capture_output=True, text=True, check=True)
            streams = json.loads(result.stdout)['streams']
            video = next(s for s in streams if s['codec_type'] == 'video')
            audio = next(s for s in streams if s['codec_type'] == 'audio')
            self.assertEqual((video['width'], video['height']), (360, 640))
            self.assertEqual(audio['sample_rate'], '48000')
            sidecar = json.loads(Path(str(output)+'.layout.json').read_text())
            self.assertEqual(len(sidecar['ranges']), 1)
            self.assertEqual(sidecar['ranges'][0]['layout'], 'gaming')


if __name__ == '__main__':
    unittest.main()
