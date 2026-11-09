"""Stable gaming composition: complete HUD overview plus a larger centre detail.

No face detector, inventory-avatar tracking, scene-dependent layout switching
or blurred duplicate. Centre detail is a fixed crop, not enemy tracking.
"""
import json
import subprocess


def filtergraph(width, height, out_w=1080, out_h=1920):
    if width <= 0 or height <= 0 or out_w % 2 or out_h % 2:
        raise ValueError('Positive source and even output dimensions required')
    overview_h = round(out_w * height / width / 2) * 2
    if width <= height:
        return (f'[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,'
                f'pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=0x10141C,'
                'setsar=1,format=yuv420p[v]')
    detail_out = min(out_w, (out_h-overview_h-int(out_h*.06))//2*2)
    if detail_out <= 0:
        raise ValueError('Output has no room for gameplay detail')
    detail = min(width, height) // 2 * 2
    # The overview retains the complete source, including corner HUD and webcam.
    # A neutral footer reserves space instead of manufacturing blurred clutter.
    return (f'[0:v]split=2[overview][detail];'
            f'[overview]scale={out_w}:{overview_h},setsar=1[top];'
            f'[detail]crop={detail}:{detail}:(iw-ow)/2:(ih-oh)/2,'
            f'scale={detail_out}:{detail_out},pad={out_w}:{detail_out}:(ow-iw)/2:0:color=0x10141C,setsar=1[bottom];'
            f'[top][bottom]vstack=inputs=2,pad={out_w}:{out_h}:0:0:color=0x10141C,'
            'format=yuv420p[v]')


def render(source, output, out_w=1080, out_h=1920):
    from ffmpeg_utils import video_encode_args, QUALITY_FAST, METADATA_SCRUB
    import layout_ranges
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(source)], capture_output=True, text=True, timeout=30)
    probe.check_returncode()
    data = json.loads(probe.stdout)
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    w, h = int(video['width']), int(video['height'])
    graph = filtergraph(w, h, out_w, out_h)
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(source),
        '-filter_complex_threads', '1', '-filter_complex', graph,
        '-map', '[v]', '-map', '0:a?', *video_encode_args(QUALITY_FAST),
        '-c:a', 'aac', '-ar', '48000', '-b:a', '192k',
        *METADATA_SCRUB, '-movflags', '+faststart', str(output)],
        capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError('Gaming composition encode failed: ' + result.stderr[-1500:])
    duration = float(data['format']['duration'])
    layout_ranges.write(str(output), [(0, duration, 'gaming')])
    return True
