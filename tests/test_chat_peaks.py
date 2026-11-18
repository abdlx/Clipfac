"""Chat-peak moments for the moment picker."""
import clipfac.chat_peaks as cp


def _messages(bursts, duration=600, base_rate=2):
    """Synthetic chat: ``bursts`` is [(second, n_messages, text)], plus a
    low baseline of plain messages so the detector has something to compare
    against. Returns [(offset, author, text)]."""
    out = []
    author = 0
    for s in range(0, duration, 30):
        for _ in range(base_rate):
            out.append((float(s), f"u{author}", "nice stream"))
            author += 1
    for second, n, text in bursts:
        for i in range(n):
            out.append((float(second + i % 5), f"burst{second}_{i}", text))
    return sorted(out)


def test_parse_twitch_vod_id():
    assert cp.parse_twitch_vod_id("https://www.twitch.tv/videos/2290012345") == "2290012345"
    assert cp.parse_twitch_vod_id("https://m.twitch.tv/videos/2290012345?t=1h2m") == "2290012345"
    assert cp.parse_twitch_vod_id("https://www.twitch.tv/somechannel") is None
    assert cp.parse_twitch_vod_id("https://clips.twitch.tv/FunnyClipSlug") is None
    assert cp.parse_twitch_vod_id("https://www.youtube.com/watch?v=abc123") is None
    assert cp.parse_twitch_vod_id(None) is None


def test_parse_youtube_id():
    assert cp.parse_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert cp.parse_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert cp.parse_youtube_id("https://www.youtube.com/shorts/abc123XYZ") == "abc123XYZ"
    assert cp.parse_youtube_id("https://www.youtube.com/live/abc123XYZ") == "abc123XYZ"
    assert cp.parse_youtube_id("https://www.twitch.tv/videos/123") is None
    assert cp.parse_youtube_id(None) is None


def test_detect_peaks_finds_hype_burst_and_labels_it():
    msgs = _messages([(300, 40, "POGGERS INSANE CLUTCH!!!")])
    peaks = cp.detect_peaks(msgs, 600)
    assert len(peaks) == 1
    p = peaks[0]
    # Chat reacts after the moment: the peak is dated back by the lag.
    assert 290 <= p["peak"] <= 300
    assert p["label"] == "hype"
    assert p["score"] >= 3.0
    assert p["start"] < p["peak"] < p["end"]


def test_detect_peaks_ignores_plain_chat_and_greetings():
    # Only greetings and plain discussion: no reaction, no peak.
    msgs = _messages([(300, 40, "hello everyone, how are you doing today")])
    assert cp.detect_peaks(msgs, 600) == []


def test_detect_peaks_ignores_a_poll_trap():
    # 60 votes of "1"/"2" in a few seconds is a poll, not a reaction.
    msgs = _messages([(300, 60, "1")])
    assert cp.detect_peaks(msgs, 600) == []


def test_detect_peaks_merges_close_bursts():
    msgs = _messages([(300, 30, "KEKW LMAO"), (306, 30, "KEKW LMAO")])
    peaks = cp.detect_peaks(msgs, 600)
    assert len(peaks) == 1


def test_detect_peaks_needs_enough_chat():
    assert cp.detect_peaks([], 600) == []
    assert cp.detect_peaks([(1.0, "u1", "POG")], 600) == []


def test_shift_peaks_moves_into_the_clipped_stretch():
    peaks = [{"start": 100, "end": 130, "peak": 115, "score": 5.0,
              "label": "hype", "said": "POG x10"},
             {"start": 3700, "end": 3730, "peak": 3715, "score": 8.0,
              "label": "laughing", "said": "KEKW x12"},
             {"start": 3590, "end": 3620, "peak": 3605, "score": 4.0,
              "label": "hype", "said": "W x5"}]
    out = cp.shift_peaks(peaks, 3600, 1800)
    assert len(out) == 2
    assert out[0]["peak"] == 115.0 and out[0]["start"] == 100.0
    assert out[1]["peak"] == 5.0 and out[1]["start"] == 0.0


def _windows(n, size=90):
    return [{"id": f"w{i}", "start": i * size, "end": (i + 1) * size, "text": ""}
            for i in range(n)]


def test_window_heat_counts_each_peak_once_by_peak_time():
    ws = _windows(3)
    peaks = [{"start": 80, "end": 110, "peak": 95, "score": 5.0,
              "label": "hype", "said": "POG x10"},
             {"start": 100, "end": 120, "peak": 110, "score": 3.0,
              "label": "laughing", "said": "KEKW x8"},
             {"start": 200, "end": 230, "peak": 215, "score": 7.0,
              "label": "a fail", "said": "F x6"}]
    heat = cp.window_heat(ws, peaks)
    assert heat["w1"]["clips"] == 2 and heat["w1"]["views"] == 800
    assert heat["w1"]["spans"] == [[80, 110], [100, 120]]
    assert heat["w1"]["labels"] == ["hype", "laughing"]
    assert heat["w2"]["clips"] == 1
    assert "w0" not in heat
