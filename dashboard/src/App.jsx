import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Youtube, Instagram, ChevronDown, Activity, Plus, X, Terminal, RotateCcw, Calendar, AlertTriangle, KeyRound, Loader2, Download } from 'lucide-react';
import MediaInput from './components/MediaInput';
import ResultCard from './components/ResultCard';
import ProcessingAnimation from './components/ProcessingAnimation';
import ThumbnailStudio from './components/ThumbnailStudio';
import SaaShortsTab from './components/SaaShortsTab';
import UGCGallery from './components/UGCGallery';
import ScheduleWeekModal from './components/ScheduleWeekModal';
import ClipEditor from './components/ClipEditor';
import ReframeEditor from './components/ReframeEditor';
import StarBanner from './components/StarBanner';
import Modal from './components/ui/Modal';
import { useAuth } from './contexts/AuthContext';
import { apiFetch, apiJson } from './lib/api';


// Simple TikTok icon sine Lucide might not have it or it varies
const TikTokIcon = ({ size = 16, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M19.589 6.686a4.793 4.793 0 0 1-3.77-4.245V2h-3.445v13.672a2.896 2.896 0 0 1-5.201 1.743l-.002-.001.002.001a2.895 2.895 0 0 1 3.183-4.51v-3.5a6.329 6.329 0 0 0-5.394 10.692 6.33 6.33 0 0 0 10.857-4.424V8.687a8.182 8.182 0 0 0 4.773 1.526V6.79a4.831 4.831 0 0 1-1.003-.104z" />
  </svg>
);

// Cloud accounts get an auto-generated opaque id (os_<hash>) as username —
// meaningless to the user, so the selector shows connected networks instead.
const isAutoProfileId = (username) => /^os_[0-9a-f]/i.test(username || "");


const formatRetention = (seconds) => {
  if (seconds >= 86400) return `${Math.round(seconds / 86400)} day${seconds >= 172800 ? 's' : ''}`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)} hour${seconds >= 7200 ? 's' : ''}`;
  return `${Math.max(1, Math.round(seconds / 60))} min`;
};

const ProfileNetworkIcons = ({ profile, size = 12 }) => (
  <span className="flex items-center gap-1.5">
    <span className={profile?.connected?.includes('tiktok') ? 'text-ink' : 'text-muted opacity-40'}>
      <TikTokIcon size={size} />
    </span>
    <span className={profile?.connected?.includes('instagram') ? 'text-ink' : 'text-muted opacity-40'}>
      <Instagram size={size} />
    </span>
    <span className={profile?.connected?.includes('youtube') ? 'text-ink' : 'text-muted opacity-40'}>
      <Youtube size={size} />
    </span>
  </span>
);

const UserProfileSelector = ({ profiles, selectedUserId, onSelect, onConnect }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!profiles || profiles.length === 0) return null;

  const selectedProfile = profiles.find(p => p.username === selectedUserId) || profiles[0];
  const autoId = isAutoProfileId(selectedProfile?.username);

  return (
    <div className="relative z-50">
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-label="social profile"
        /* Phone: avatar + chevron only. A 180px pill next to the menu button,
           the section title and the minutes meter overflowed a 360px header. */
        className="flex items-center justify-between gap-1 bg-paper2 border border-rule2 rounded-input px-2 sm:px-3 py-2 text-sm text-ink2 hover:bg-paper3 transition-colors sm:min-w-[180px]"
      >
        <span className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-paper3 border border-rule flex items-center justify-center font-mono text-micro text-brass shrink-0">
            {autoId ? "S" : (selectedProfile?.username?.substring(0, 1).toUpperCase() || "U")}
          </div>
          {autoId ? (
            <span className="hidden sm:flex"><ProfileNetworkIcons profile={selectedProfile} size={13} /></span>
          ) : (
            <span className="hidden sm:block font-medium text-ink truncate max-w-[100px]">{selectedProfile?.username || "Select User"}</span>
          )}
        </span>
        <ChevronDown size={14} className={`text-muted transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full mt-2 right-0 w-64 card overflow-hidden">
          <div className="max-h-60 overflow-y-auto custom-scrollbar">
            {profiles.map((profile) => (
              <button
                key={profile.username}
                onClick={() => {
                  onSelect(profile.username);
                  setIsOpen(false);
                }}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-paper3 transition-colors text-left group border-b border-rule last:border-0"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-paper3 flex items-center justify-center font-mono text-micro text-ink border border-rule shrink-0">
                    {isAutoProfileId(profile.username) ? "S" : profile.username.substring(0, 2).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink2 group-hover:text-ink transition-colors truncate">
                      {isAutoProfileId(profile.username)
                        ? `Social profile ${profiles.indexOf(profile) + 1}`
                        : profile.username}
                    </div>
                    <div className="flex gap-2 mt-0.5">
                      {/* Status indicators */}
                      <div className={`flex items-center gap-1 ${profile.connected.includes('tiktok') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <TikTokIcon size={10} />
                      </div>
                      <div className={`flex items-center gap-1 ${profile.connected.includes('instagram') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <Instagram size={10} />
                      </div>
                      <div className={`flex items-center gap-1 ${profile.connected.includes('youtube') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <Youtube size={10} />
                      </div>
                    </div>
                  </div>
                </div>
                {selectedUserId === profile.username && <Check size={14} className="text-brass shrink-0" />}
              </button>
            ))}
          </div>
          {/* For managed users this dropdown otherwise does nothing (one profile,
              nothing to switch) — its real job is being the door to connecting
              the greyed-out networks it displays. */}
          {onConnect && (
            <button
              onClick={() => { setIsOpen(false); onConnect(); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-brass hover:bg-paper3 transition-colors text-left border-t border-rule"
            >
              <Share2 size={14} /> Connect / manage accounts
            </button>
          )}
        </div>
      )}
    </div>
  );
};

const SESSION_KEY = 'openshorts_session';

// The server's own explanation for a rejected/failed job, readable: FastAPI
// answers {"detail": "..."} or {"detail": {"message": ...}}. Showing the raw
// JSON (or a generic "failed") hid actionable reasons such as "this video is
// only 30s long", and new users in the tutorial saw only "That run failed".
const readableError = (raw) => {
  const text = String(raw || '').trim();
  try {
    const body = JSON.parse(text);
    const d = body?.detail ?? body;
    if (typeof d === 'string' && d) return d.slice(0, 300);
    if (d && typeof d.message === 'string') return d.message.slice(0, 300);
  } catch (_) { /* not JSON */ }
  return text.replace(/^Error:\s*/, '').slice(0, 300) || 'Something went wrong.';
};
// Matches the self-host JOB_RETENTION_SECONDS default. A restore whose job was
// already purged server-side fails gracefully and clears the saved session.
const SESSION_MAX_AGE = 86400000; // 24 hours

// Mock polling function
const pollJob = async (jobId) => {
  const res = await apiFetch(`/api/status/${jobId}`);
  if (!res.ok) {
    const err = new Error('Status check failed');
    err.status = res.status;
    throw err;
  }
  return res.json();
};

function App({ embedded = false, initialTab, vaultKeys = {}, onNavigateToSettings } = {}) {
  // Auth session (self-host: billing is disabled, so only the session bits
  // below are live — refreshMe, the retention window, and the local LLM).
  const { isManaged, refreshMe, jobRetentionSeconds, localLlm } = useAuth();
  // {position, ahead, eta_seconds} while the job waits in line, else null.
  const [queueInfo, setQueueInfo] = useState(null);
  // Why the last job could not start or failed, in plain words (or '').
  const [jobError, setJobError] = useState('');

  const [uploadUserId, setUploadUserId] = useState(() => localStorage.getItem('uploadUserId') || '');
  const [userProfiles, setUserProfiles] = useState([]); // List of {username, connected: []}
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, processing, complete, error
  const [results, setResults] = useState(null);
  // Best clips first. The backend hands them back in transcript order, which
  // buries the strongest one wherever it happens to fall in the video — and
  // the first card is the one people actually watch and publish.
  //
  // The ORIGINAL array position travels with each clip and is what gets passed
  // down as `index`: it is the clip's identity everywhere else (clip_index on
  // /api/subtitle, /api/edit and publishing, the clip-N.mp4 download name, the
  // saved per-clip project state). Sorting the array itself would silently
  // repoint all of that at the wrong clip.
  const rankedClips = useMemo(() => {
    const clips = results?.clips;
    if (!Array.isArray(clips)) return [];
    return clips
      .map((clip, index) => ({ clip, index }))
      .sort((a, b) => {
        const sa = Number.isFinite(a.clip?.predicted_score) ? a.clip.predicted_score : -1;
        const sb = Number.isFinite(b.clip?.predicted_score) ? b.clip.predicted_score : -1;
        // Ties (and clips with no score at all) keep transcript order.
        return sb - sa || a.index - b.index;
      });
  }, [results]);
  // Bulk subtitles: apply one style to every clip of the job (triggered from
  // within a clip's subtitle modal via "apply to all").
  const [bulkSub, setBulkSub] = useState({ running: false, current: 0, total: 0, errors: 0 });
  const [downloadingAll, setDownloadingAll] = useState(false);
  // Pre-flight quality gate: { info: {max_height, min_height, cookies_invalid}, data }
  const [qualityGate, setQualityGate] = useState(null);
  const [logs, setLogs] = useState([]);
  // When each server log line arrived (epoch s, parallel to logs), from /api/status.
  const [logTimes, setLogTimes] = useState([]);
  // Collapsed on phones: the log tail is the least useful thing on a 360px
  // screen and it was pushing the actual clips a full scroll down.
  const [logsVisible, setLogsVisible] = useState(() => {
    try { return window.innerWidth >= 768; } catch { return true; }
  });
  const [processingMedia, setProcessingMedia] = useState(null);
  const [activeTab, setActiveTab] = useState(initialTab || 'dashboard'); // dashboard, settings

  // Embedded in the workspace shell, the shell drives the tab (Projects nav).
  useEffect(() => {
    if (embedded && initialTab) setActiveTab(initialTab);
  }, [initialTab, embedded]);

  const [sessionRecovered, setSessionRecovered] = useState(false);
  const [showScheduleWeek, setShowScheduleWeek] = useState(false);
  // Clip editor overlay: index of the clip being edited, or null.
  const [editingClip, setEditingClip] = useState(null);
  const [reframingClip, setReframingClip] = useState(null);

  // Sync state for original video playback
  const [syncedTime, setSyncedTime] = useState(0);
  const [isSyncedPlaying, setIsSyncedPlaying] = useState(false);
  const [syncTrigger, setSyncTrigger] = useState(0);

  const handleClipPlay = (startTime) => {
    setSyncedTime(startTime);
    setIsSyncedPlaying(true);
    setSyncTrigger(prev => prev + 1);
  };

  const handleClipPause = () => {
    setIsSyncedPlaying(false);
  };

  // A recut replaced the clip's server file with a fresh render (burned layers
  // reset), so update the results and let the ResultCard remount from the new file.
  const handleClipRerendered = (index, data) => {
    setResults((prev) => {
      if (!prev?.clips?.[index]) return prev;
      const clips = prev.clips.slice();
      clips[index] = {
        ...clips[index],
        video_url: data.new_video_url,
        start: data.start,
        end: data.end,
        recipe: data.recipe,
      };
      return { ...prev, clips };
    });
  };

  // Apply one subtitle style to every clip of the job, sequentially.
  const handleBulkSubtitles = async (options) => {
    const clips = results?.clips || [];
    const total = clips.length;
    if (!total) return;
    setBulkSub({ running: true, current: 0, total, errors: 0 });
    let errors = 0;
    for (let i = 0; i < total; i++) {
      setBulkSub({ running: true, current: i + 1, total, errors });
      try {
        const res = await apiFetch('/api/subtitle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_id: jobId,
            clip_index: i,
            position: options.position,
            font_size: options.fontSize,
            font_name: options.fontName,
            font_color: options.fontColor,
            border_color: options.borderColor,
            border_width: options.borderWidth,
            bg_color: options.bgColor,
            bg_opacity: options.bgOpacity,
            style: options.style || 'pill',
            highlight_color: options.highlightColor || '#FFD700',
            effect: options.effect || 'none',
            base_opacity: options.baseOpacity ?? 1.0,
            uppercase: options.uppercase || false,
            reveal: options.reveal || false,
            shadow: options.shadow || 0,
            max_chars: options.maxChars ?? null,
            max_duration: options.maxDuration ?? null,
            // Chain from the clip's current server file (its video_url basename).
            input_filename: (clips[i].video_url || '').split('/').pop(),
          }),
        });
        if (!res.ok) errors++;
      } catch {
        errors++;
      }
    }
    setBulkSub({ running: false, current: total, total, errors });
    refreshMe();
    // Refresh results so each ResultCard picks up its new subtitled video_url.
    try {
      const data = await pollJob(jobId);
      if (data.result) setResults(data.result);
    } catch { /* keep current results */ }
  };

  const handleDownloadAll = async () => {
    if (!jobId) return;
    setDownloadingAll(true);
    try {
      const res = await apiFetch(`/api/jobs/${jobId}/download-all`);
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `openshorts_clips_${(jobId || '').slice(0, 8)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Download failed: ${e.message}`);
    } finally {
      setDownloadingAll(false);
    }
  };

  // Session Recovery: Restore on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(SESSION_KEY);
      if (!saved) return;
      const session = JSON.parse(saved);
      if (Date.now() - session.timestamp > SESSION_MAX_AGE) {
        localStorage.removeItem(SESSION_KEY);
        return;
      }
      if (session.jobId && session.status && session.status !== 'idle') {
        setJobId(session.jobId);
        setResults(session.results || null);
        // Restore the source preview. Older sessions (or uploads) saved no
        // media, so fall back to the backend-served source for this job.
        if (session.processingMedia) setProcessingMedia(session.processingMedia);
        else setProcessingMedia({ type: 'server', payload: `/api/source/${session.jobId}` });
        if (session.activeTab) setActiveTab(session.activeTab);
        // If was processing, resume polling; if complete/error, just show results
        setStatus(session.status === 'processing' ? 'processing' : session.status);
        setSessionRecovered(true);
        setTimeout(() => setSessionRecovered(false), 5000);
      }
    } catch (e) {
      localStorage.removeItem(SESSION_KEY);
    }
  }, []);

  // Session Recovery: Save state changes
  useEffect(() => {
    if (status === 'idle') {
      localStorage.removeItem(SESSION_KEY);
      return;
    }
    try {
      // URL (YouTube) media serializes as-is. Uploaded 'file' media is a blob
      // that can't be persisted, so point the recovered preview at the source
      // served by the backend instead of dropping it.
      let persistMedia = null;
      if (processingMedia?.type === 'url') persistMedia = processingMedia;
      else if (processingMedia && jobId) persistMedia = { type: 'server', payload: `/api/source/${jobId}` };
      const sessionData = {
        jobId,
        status,
        results,
        processingMedia: persistMedia,
        activeTab,
        timestamp: Date.now()
      };
      localStorage.setItem(SESSION_KEY, JSON.stringify(sessionData));
    } catch (e) {
      // localStorage full or serialization error - ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, status, results, activeTab]);

  // The selected social profile id is the only key the editor still keeps in
  // this browser. Provider keys live in the vault (Settings → Provider
  // connections); the server injects them on every API call.
  useEffect(() => {
    if (uploadUserId) {
      localStorage.setItem('uploadUserId', uploadUserId);
    }
  }, [uploadUserId]);

  useEffect(() => {
    if (userProfiles.length === 0) {
      fetchUserProfiles({ silent: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  useEffect(() => {
    let interval;
    if ((status === 'processing' || status === 'completed') && jobId) {
      interval = setInterval(async () => {
        try {
          const data = await pollJob(jobId);
          console.log("Job status:", data);

          // Update results if available (real-time)
          if (data.result) {
            setResults(data.result);
          }

          setQueueInfo(data.status === 'queued' && data.queue ? data.queue : null);

          if (data.status === 'completed') {
            setQueueInfo(null);
            setStatus('complete');
            clearInterval(interval);
            refreshMe();
          } else if (data.status === 'failed') {
            setStatus('error');
            const errorMsg = data.error || (data.logs && data.logs.length > 0 ? data.logs[data.logs.length - 1] : "Process failed");
            setJobError(readableError(errorMsg));
            setLogs(prev => [...prev, "Error: " + errorMsg]);
            clearInterval(interval);
            refreshMe();
          } else {
            // Update logs if available
            if (data.logs) {
              setLogs(data.logs);
              setLogTimes(data.log_times || []);
            }
          }
        } catch (e) {
          // A 401 means the saved installation token is gone (logged out):
          // stop polling instead of hammering the API — and the console —
          // forever. The workspace login is the way back in.
          if (e?.status === 401) { clearInterval(interval); return; }
          console.error("Polling error", e);
        }
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [status, jobId, refreshMe]);


  // silent: background auto-fetch — never alert(), just log. The server
  // resolves the Upload-Post key from the saved workspace connection, so this
  // runs with no key in the browser.
  const fetchUserProfiles = async ({ silent = false } = {}) => {
    try {
      const res = await apiFetch('/api/social/user');
      // 400 is the server's "Upload-Post is not connected" — a normal state
      // when no key is saved yet, not a failure. Treat it as zero profiles
      // instead of logging an error: the publish UI already shows the
      // not-connected state, and a real key problem still surfaces as 401.
      if (res.status === 400) { setUserProfiles([]); return; }
      if (!res.ok) throw new Error("Failed to fetch");
      const data = await res.json();
      if (data.profiles && data.profiles.length > 0) {
        setUserProfiles(data.profiles);
        // Auto select first if none selected
        if (!uploadUserId) {
          setUploadUserId(data.profiles[0].username);
        }
      } else if (!silent) {
        alert("No profiles found for this API Key.");
      }
    } catch (e) {
      if (!silent) alert("Error fetching User Profiles. Please check key.");
      console.error(e);
    }
  };

  // Hosted is paid-only (no BYOK core). Self-host uses BYOK keys.
  // `keysMissing` now means "self-host BYOK keys missing" — it never fires on hosted.
  // A self-hosted server running the moment picker on a local LLM
  // (LLM_BASE_URL) does not need a Gemini key for the core pipeline.
  // Upload-Post is not part of this gate: publishing resolves the saved
  // workspace connection server-side, so the editor never needs the key.
  // The vault is canonical: a key saved in Settings → Provider connections
  // counts, as does a local LLM. The editor never asks for a browser key.
  const geminiOk = !!vaultKeys.gemini || !!localLlm;
  const keysMissing = !geminiOk;

  // Managed users connect their socials via Upload-Post's branded hosted page.
  const handleConnectSocials = async () => {
    try {
      const { access_url } = await apiJson('/api/social/connect', { method: 'POST' });
      // Same tab so the connect page's redirectUrl brings the user back into the app.
      if (access_url) window.location.href = access_url;
    } catch (e) {
      alert('Could not open the connection page. Please try again.');
    }
  };

  // Open the Upload-Post white-label page (which includes the scheduling calendar)
  // in a new tab, for consulting/managing scheduled posts from the dashboard.
  const handleOpenCalendar = async () => {
    try {
      const { access_url } = await apiJson('/api/social/connect', { method: 'POST' });
      if (access_url) window.open(access_url, '_blank', 'noopener');
    } catch (e) {
      alert('Could not open the calendar. Please try again.');
    }
  };

  const handleProcess = async (data, forceLowQuality = false) => {
    // Self-host: the vault holds the keys. A missing Gemini key (and no local
    // LLM) blocks the job with a plain message — Settings is where it goes.
    if (keysMissing) {
      setJobError('Add a Gemini key in Settings → Provider connections to start a job.');
      return;
    }
    setStatus('processing');
    setJobError('');
    setLogs(["Starting process..."]);
    setLogTimes([Date.now() / 1000]);
    setResults(null);
    // Studio handovers have no local media object; the preview switches to the
    // backend-served source once the job id is known.
    setProcessingMedia(data.type === 'thumbnail_session' ? null : data);
    setQualityGate(null);

    try {
      let body;
      // No key headers here: the server injects the vault keys on every call.
      const headers = {};

      // Advanced generation controls: only sent when the user set them, so the
      // default request stays byte-identical to the pre-feature one.
      const advanced = {
        target_clips: data.targetClips || null,
        clip_min_seconds: data.clipMinSeconds || null,
        clip_max_seconds: data.clipMaxSeconds || null,
        // Sent explicitly both ways: absent means off for raw API callers,
        // but the dashboard always states the user's choice.
        auto_hook: data.autoHook ? '1' : '0',
        auto_hook_style: data.autoHook ? (data.autoHookStyle || 'pill') : null,
        // 'auto' is the server default, so only a deliberate choice travels.
        layouts: data.layout && data.layout !== 'auto' ? data.layout : null,
        // The stretch picked under the cost line (seconds); absent = whole video.
        source_start: data.sourceStart ?? null,
        source_end: data.sourceEnd ?? null,
      };

      if (data.type === 'url') {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify({
          url: data.payload,
          acknowledged: !!data.acknowledged,
          output_format: data.outputFormat || 'auto',
          force_low_quality: forceLowQuality,
          ...Object.fromEntries(Object.entries(advanced).filter(([, v]) => v != null)),
        });
      } else if (data.type === 'thumbnail_session') {
        // Handover from Thumbnail Studio (issue #68): the video and transcript
        // already live server-side, keyed by the Studio session.
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify({
          thumbnail_session_id: data.payload,
          acknowledged: !!data.acknowledged,
          output_format: data.outputFormat || 'auto',
          ...Object.fromEntries(Object.entries(advanced).filter(([, v]) => v != null)),
        });
      } else {
        const formData = new FormData();
        formData.append('file', data.payload);
        formData.append('acknowledged', data.acknowledged ? 'true' : 'false');
        formData.append('output_format', data.outputFormat || 'auto');
        for (const [k, v] of Object.entries(advanced)) {
          if (v != null) formData.append(k, v);
        }
        body = formData;
      }

      const res = await apiFetch('/api/process', { method: 'POST', headers, body });

      if (!res.ok) throw new Error(await res.text());
      const resData = await res.json();

      // Quality gate: the source is below the min resolution — ask before burning
      // 20 min on it. On confirm we resend with force_low_quality.
      if (resData.needs_confirmation) {
        setStatus('idle');
        setQualityGate({ info: resData.quality_check, data });
        return;
      }

      setJobId(resData.job_id);
      if (data.type === 'thumbnail_session') {
        setProcessingMedia({ type: 'server', payload: `/api/source/${resData.job_id}` });
      }
      // Minutes are reserved at job start, not at complete.
      refreshMe();

    } catch (e) {
      const reason = readableError(e.message);
      setJobError(reason);
      setStatus('error');
      setLogs(l => [...l, `Error starting job: ${reason}`]);
    }
  };

  const handleReset = () => {
    setStatus('idle');
    setJobId(null);
    setResults(null);
    setLogs([]);
    setLogTimes([]);
    setProcessingMedia(null);
    setQueueInfo(null);
    setJobError('');
    localStorage.removeItem(SESSION_KEY);
  };

  // --- UI Components ---

  const goToTab = (id) => {
    // Settings lives in the shell — hand the navigation to it.
    if (id === 'settings') { onNavigateToSettings?.(); return; }
    setActiveTab(id);
  };
  return (
    /* h-dvh where supported: on mobile Safari/Chrome `100vh` is the tallest the
       viewport ever gets, so a h-screen shell hides its own bottom bar behind
       the browser chrome until the user scrolls. */
    <div className="flex flex-1 min-h-0 flex-col bg-paper overflow-hidden">

      <main className="flex-1 min-w-0 flex flex-col h-full overflow-hidden relative">
        {/* Top Header */}
        <header className="h-14 border-b border-rule bg-paper flex items-center justify-between gap-2 px-3 sm:px-6 shrink-0 z-10">
          <div className="flex items-center gap-2 sm:gap-4 min-w-0">
            {status !== 'idle' && (
              <button
                onClick={handleReset}
                className="btn-quiet px-3 py-1.5 text-xs shrink-0"
                aria-label="New Project"
              >
                <Plus size={14} />
                <span className="hidden sm:inline">New Project</span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 sm:gap-4 shrink-0">
            {userProfiles.length > 0 && (
              <UserProfileSelector
                profiles={userProfiles}
                selectedUserId={uploadUserId}
                onSelect={setUploadUserId}
                onConnect={isManaged ? handleConnectSocials : undefined}
              />
            )}

            {/* Hidden below sm: the standing banner underneath already says the
                same thing, and two warnings in a 360px header is just noise. */}
            {keysMissing && (
              <button
                onClick={() => goToTab('settings')}
                className="badge-warn hover:brightness-125 transition-all hidden sm:inline-flex"
                title="Save a Gemini key in Settings → Provider connections"
              >
                <AlertTriangle size={12} />
                <span className="hidden md:inline">
                  {'Gemini API Key Missing'}
                </span>
                <span className="md:hidden">keys missing</span>
              </button>
            )}
          </div>
        </header>

        {/* Persistent Missing Keys Banner — visible on every screen */}
        {keysMissing && (
          <div className="mx-3 sm:mx-6 mt-3 px-3.5 sm:px-4 py-3 bg-paper2 border border-rule rounded-card flex flex-wrap items-center justify-between gap-2.5 sm:gap-4 shrink-0 animate-fade">
            <div className="flex items-start sm:items-center gap-2.5 sm:gap-3 text-sm text-ink2 min-w-0 flex-1">
              <KeyRound size={16} className="shrink-0 text-warn mt-0.5 sm:mt-0" />
              <div className="min-w-0">
                <span className="font-medium text-ink">Required API keys missing.</span>{' '}
                <span className="text-muted">
                  {'Save a Gemini key in Settings → Provider connections to use Clipfac.'}
                </span>
              </div>
            </div>
            <button
              onClick={() => goToTab('settings')}
              className="btn-quiet px-3 py-1.5 text-xs shrink-0 w-full sm:w-auto"
            >
              Go to Settings
            </button>
          </div>
        )}

        {/* Session Recovery Banner */}
        {sessionRecovered && (
          <div className="mx-3 sm:mx-6 mt-2 px-3.5 sm:px-4 py-3 bg-paper2 border border-rule rounded-card flex items-start justify-between gap-3 animate-fade shrink-0">
            <div className="flex items-start sm:items-center gap-2 text-sm text-ink2 flex-wrap min-w-0">
              <RotateCcw size={16} className="text-brass shrink-0 mt-0.5 sm:mt-0" />
              <span className="font-medium">Session recovered</span>
              <span className="text-muted text-xs">Your previous work has been restored.</span>
            </div>
            <button
              onClick={() => setSessionRecovered(false)}
              aria-label="dismiss"
              className="text-muted hover:text-ink transition-colors shrink-0 -m-1 p-1"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {/* Main Workspace */}
        <div className="flex-1 overflow-hidden relative">

          {/* View: SaaS Shorts */}
          {activeTab === 'saasshorts' && (
            <SaaShortsTab vaultKeys={vaultKeys} uploadUserId={uploadUserId} managed={isManaged} onOpenSettings={() => goToTab('settings')} />
          )}

          {/* View: UGC Gallery */}
          {activeTab === 'ugc-gallery' && (
            <div className="h-full overflow-y-auto custom-scrollbar animate-fade">
              <div className="max-w-6xl mx-auto p-4 sm:p-6 md:p-8">
                <UGCGallery />
              </div>
            </div>
          )}



          {activeTab === 'thumbnails' && (
            <ThumbnailStudio
              vaultKeys={vaultKeys}
              uploadUserId={uploadUserId}
              managed={isManaged}
              onOpenSettings={() => goToTab('settings')}
              onCreateClips={(sessionId) => {
                setActiveTab('dashboard');
                // The Studio source is the user's own upload, published to their
                // own channel; the handover carries that same attestation.
                handleProcess({ type: 'thumbnail_session', payload: sessionId, acknowledged: true });
              }}
            />
          )}

          {/* View: Gallery */}
          {/* {activeTab === 'gallery' && (
            <Gallery />
          )} */}

          {/* View: Dashboard (Idle) */}
          {activeTab === 'dashboard' && status === 'idle' && (
            <div className="h-full overflow-y-auto custom-scrollbar animate-fade">
              <div className="min-h-full flex flex-col items-center justify-center px-4 py-5 sm:p-6">
              {/* On a phone the hero used to fill the fold on its own and push
                  the uploader — the whole point of the screen — below it. The
                  eyebrow, the display size and the gaps all shrink first. */}
              <div className="max-w-xl w-full text-center space-y-5 sm:space-y-8">
                <div className="space-y-2.5 sm:space-y-4">
                  <p className="eyebrow hidden sm:block">01 · CLIP GENERATOR</p>
                  <h1 className="font-display lowercase text-3xl sm:text-4xl md:text-5xl text-ink">
                    Create your next clip
                  </h1>
                  <p className="text-muted text-[15px] sm:text-lg leading-snug sm:leading-normal max-w-sm sm:max-w-none mx-auto">
                    Turn long-form footage into edited clips with captions and subject tracking.
                  </p>
                  {/* The same pipeline is an MCP server: point people at the
                      one place that explains how to drive it from an agent. */}
                  <p className="text-xs text-muted">
                    Or let an agent do it:{' '}
                    <a
                      href="#app?tab=settings"
                      onClick={(e) => { e.preventDefault(); goToTab('settings'); }}
                      className="text-ink2 underline underline-offset-2 hover:text-brass transition-colors"
                    >
                      connect Claude, ChatGPT or n8n →
                    </a>
                  </p>
                </div>

                <MediaInput onProcess={handleProcess} isProcessing={status === 'processing'} />

                <div className="flex flex-wrap items-center justify-center gap-4 sm:gap-8 text-muted text-xs sm:text-sm">
                  <span className="flex items-center gap-2"><Youtube size={16} /> YouTube</span>
                  <span className="flex items-center gap-2"><Instagram size={16} /> Instagram</span>
                  <span className="flex items-center gap-2"><TikTokIcon size={16} /> TikTok</span>
                </div>
              </div>
              </div>
            </div>
          )}

          {/* View: Processing / Results (Split View) */}
          {activeTab === 'dashboard' && (status === 'processing' || status === 'complete' || status === 'error') && (
            <div className="h-full flex flex-col md:flex-row gap-3 md:gap-4 p-3 md:p-4 overflow-y-auto md:overflow-y-hidden custom-scrollbar animate-fade">

              {/* Left Panel: Preview & Status */}
              <div className={`${status === 'complete' ? 'w-full md:w-[30%] lg:w-[25%]' : 'w-full md:w-[55%] lg:w-[60%]'} md:h-full flex flex-col shrink-0 md:shrink card p-3.5 sm:p-6 md:overflow-y-auto custom-scrollbar transition-all duration-700 ease-in-out`}>
                <div className="mb-4 sm:mb-6 flex items-center justify-between gap-2">
                  <h2 className="text-sm font-medium text-ink lowercase flex items-center gap-2">
                    <Activity className={`text-brass ${status === 'processing' ? 'animate-pulse' : ''}`} size={18} />
                    Live Analysis
                  </h2>
                  <span className={status === 'processing' ? 'badge-brass' :
                    status === 'complete' ? 'badge-ok' :
                      'badge-danger'
                    }>
                    {status.toUpperCase()}
                  </span>
                </div>

                {/* Waiting in line: say where and for how long. */}
                {status === 'processing' && queueInfo && (
                  <div className="mb-4 rounded-card border border-brass/40 bg-brass/5 px-4 py-3 text-sm">
                    <p className="text-ink">
                      {queueInfo.ahead === 0
                        ? 'You are next in line. Starting in a moment…'
                        : <>You are <b>#{queueInfo.position}</b> in line · about <b>{Math.max(1, Math.round(queueInfo.eta_seconds / 60))} min</b></>}
                    </p>
                  </div>
                )}

                {/* Video Preview */}
                {processingMedia && (
                  <ProcessingAnimation
                    media={processingMedia}
                    isComplete={status === 'complete'}
                    syncedTime={syncedTime}
                    isSyncedPlaying={isSyncedPlaying}
                    syncTrigger={syncTrigger}
                  />
                )}

                {/* Phones only. The scan box drops its invented telemetry at
                    this size and the log terminal below starts collapsed, so
                    without this the screen would say nothing about what the job
                    is actually doing. The log tail is the real answer. */}
                {status === 'processing' && (
                  <div className="sm:hidden mb-3 flex items-start gap-2 text-xs text-ink2 min-w-0">
                    <Loader2 size={14} className="animate-spin text-brass shrink-0 mt-px" />
                    <span className="min-w-0 leading-snug break-words">
                      {logs.length ? logs[logs.length - 1] : 'starting up…'}
                    </span>
                  </div>
                )}

                {/* The render is dead time: the user is watching a progress bar
                    with nothing to do, so this is where the one star ask goes. */}
                {status === 'processing' && (
                  <div className="my-3">
                    <StarBanner message="Got a minute while this renders?" />
                  </div>
                )}

                {/* Logs Terminal */}
                <div className={`bg-paper rounded-card border border-rule overflow-hidden flex flex-col transition-all duration-500 ${status === 'complete' ? `min-h-0 opacity-50 hover:opacity-100 ${logsVisible ? 'h-32' : 'h-auto'}` : `flex-1 ${logsVisible ? 'min-h-[160px] sm:min-h-[200px]' : 'min-h-0 flex-none'}`}`}>
                  <button
                    type="button"
                    onClick={() => setLogsVisible(!logsVisible)}
                    aria-expanded={logsVisible}
                    className="w-full px-3.5 sm:px-4 py-2.5 border-b border-rule flex items-center justify-between gap-2 bg-paper2 shrink-0 text-left"
                  >
                    <span className="readout flex items-center gap-2">
                      <Terminal size={12} /> System Logs
                    </span>
                    <span className="flex items-center gap-2 text-muted">
                      {!logsVisible && logs.length > 0 && (
                        <span className="readout normal-case">{logs.length}</span>
                      )}
                      <ChevronDown size={16} className={logsVisible ? '' : 'rotate-180'} />
                    </span>
                  </button>
                  {logsVisible && (
                    <div className="flex-1 p-3.5 sm:p-4 overflow-y-auto font-mono text-[11px] sm:text-xs space-y-1.5 custom-scrollbar text-muted break-words">
                      {logs.map((log, i) => (
                        <div key={i} className={`flex gap-2 ${log.toLowerCase().includes('error') ? 'text-danger' : 'text-muted'}`}>
                          <span className="text-muted opacity-50 shrink-0 hidden sm:inline tabular-nums">
                            {logTimes[i] ? new Date(logTimes[i] * 1000).toLocaleTimeString() : ''}
                          </span>
                          <span className="min-w-0 break-words">{log}</span>
                        </div>
                      ))}
                      {status === 'processing' && (
                        <div className="animate-pulse text-brass">_</div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Right Panel: Results Grid */}
              <div className={`${status === 'complete' ? 'w-full md:w-[70%] lg:w-[75%]' : 'w-full md:w-[45%] lg:w-[40%]'} md:h-full flex flex-col shrink-0 md:shrink card p-3.5 sm:p-6 transition-all duration-700 ease-in-out`}>
                {/* Title + counters on one row, the two actions on their own row
                    below. Wrapping them all together dropped a lone half-width
                    "schedule week" pill under the title on a phone. */}
                <div className="mb-4 sm:mb-6 shrink-0 space-y-3">
                  <h2 className="font-display lowercase text-lg sm:text-xl text-ink flex flex-wrap items-center gap-2">
                    <span className="mr-auto">Generated Shorts</span>
                    {results?.clips?.length > 0 && (
                      <span className="readout bg-paper3 px-2.5 py-1 rounded-full">
                        {results.clips.length} Clips
                      </span>
                    )}
                    {results?.cost_analysis && !isManaged && (
                      <span className="readout bg-paper3 px-2.5 py-1 rounded-full" title={`Input: ${results.cost_analysis.input_tokens} | Output: ${results.cost_analysis.output_tokens}`}>
                        GEMINI · ${results.cost_analysis.total_cost.toFixed(5)}
                      </span>
                    )}
                  </h2>
                  {results?.clips?.length > 0 && status === 'complete' && (
                    <div className="flex flex-col sm:flex-row sm:justify-end items-stretch sm:items-center gap-2">
                      <button
                        onClick={handleDownloadAll}
                        disabled={downloadingAll}
                        className="btn-ghost px-3 py-2 text-xs"
                        title="Download all clips as a ZIP"
                      >
                        {downloadingAll
                          ? <><Loader2 size={14} className="animate-spin" />zipping…</>
                          : <><Download size={14} />download all</>}
                      </button>
                      {results.clips.length > 1 && (
                        <button
                          onClick={() => setShowScheduleWeek(true)}
                          className="btn-primary px-4 py-2 text-xs"
                        >
                          <Calendar size={14} />
                          schedule week
                        </button>
                      )}
                    </div>
                  )}
                </div>

                {status === 'complete' && results?.clips?.length > 0 && (
                  <div className="mb-2 space-y-2">
                    {/* Self-host only: cloud archives clips to the video library,
                        here they really are gone once the retention sweep runs. */}
                    {jobRetentionSeconds > 0 && (
                      <div className="px-3 py-2.5 rounded-input bg-paper3 border border-paper3 text-sm">
                        <span className="text-ink">Clips are kept for {formatRetention(jobRetentionSeconds)}, then deleted.</span>{' '}
                        <span className="text-muted">Download what you want to keep, or raise JOB_RETENTION_SECONDS in your env.</span>
                      </div>
                    )}
                  </div>
                )}

                <div className="flex-1 overflow-y-auto custom-scrollbar p-1">
                  {results && results.clips && results.clips.length > 0 ? (
                    <div className={`grid gap-4 pb-10 ${status === 'complete' ? 'grid-cols-[repeat(auto-fill,minmax(min(100%,600px),1fr))]' : 'grid-cols-1'}`}>
                      {rankedClips.map(({ clip, index: i }) => (
                        <ResultCard
                          key={`${jobId}-${i}-${clip.video_url || ''}`}
                          clip={clip}
                          index={i}
                          jobId={jobId}
                          onEditClip={(index) => setEditingClip(index)}
                          onReframeClip={(index) => setReframingClip(index)}
                          vaultKeys={vaultKeys}
                          uploadUserId={uploadUserId}
                          isManaged={isManaged}
                          connectedPlatforms={(userProfiles.find((p) => p.username === uploadUserId) || userProfiles[0])?.connected ?? null}
                          onConnectSocials={isManaged ? handleConnectSocials : null}
                          onPlay={(time) => handleClipPlay(time)}
                          onPause={handleClipPause}
                          onBulkSubtitle={handleBulkSubtitles}
                          clipCount={results.clips.length}
                          bulkProgress={bulkSub}
                        />
                      ))}
                    </div>
                  ) : (
                    status === 'processing' ? (
                      <div className="h-full min-h-[140px] flex flex-col items-center justify-center text-muted space-y-3 text-center px-4">
                        <Loader2 size={28} className="animate-spin text-brass" />
                        <p className="text-sm lowercase">Waiting for clips...</p>
                        <p className="text-xs text-muted/80 max-w-[26ch] leading-snug">
                          They appear here one by one as each finishes rendering.
                        </p>
                      </div>
                    ) : status === 'error' ? (
                      <div className="h-full min-h-[120px] flex flex-col items-center justify-center text-danger space-y-2">
                        <p>Generation failed.</p>
                      </div>
                    ) : null
                  )}
                </div>
              </div>

            </div>
          )}

        </div>

      </main>

      <ScheduleWeekModal
        isOpen={showScheduleWeek}
        onClose={() => setShowScheduleWeek(false)}
        clips={results?.clips || []}
        jobId={jobId}
        uploadUserId={uploadUserId}
      />

      {/* Pre-flight quality gate */}
      {qualityGate && (
        <Modal isOpen={true} onClose={() => setQualityGate(null)} size="md" eyebrow="HEADS UP" title="low source quality">
          <div className="space-y-4">
            <p className="text-sm text-ink2">
              YouTube only offers <span className="text-brass font-semibold">{qualityGate.info.max_height}p</span> for this video
              (below the {qualityGate.info.min_height}p we recommend). Processing anyway will produce lower-quality clips.
            </p>
            {qualityGate.info.cookies_invalid && (
              <p className="text-xs text-muted">
                Your YouTube cookies look expired — refreshing them (export again from an incognito window) often unlocks HD.
              </p>
            )}
            <div className="flex gap-2 justify-end pt-2">
              <button onClick={() => setQualityGate(null)} className="btn-ghost">cancel</button>
              <button
                onClick={() => { const d = qualityGate.data; setQualityGate(null); handleProcess(d, true); }}
                className="btn-primary"
              >
                process anyway
              </button>
            </div>
          </div>
        </Modal>
      )}


      {editingClip !== null && results?.clips?.[editingClip] && (
        <ClipEditor
          jobId={jobId}
          clipIndex={editingClip}
          clipTitle={results.clips[editingClip].video_title_for_youtube_short || ''}
          onClose={() => setEditingClip(null)}
          onRerendered={handleClipRerendered}
        />
      )}
      {reframingClip !== null && results?.clips?.[reframingClip] && (
        <ReframeEditor
          jobId={jobId}
          clipIndex={reframingClip}
          clipTitle={results.clips[reframingClip].video_title_for_youtube_short || ''}
          onClose={() => setReframingClip(null)}
          onReframed={handleClipRerendered}
        />
      )}
    </div>
  );
}

export default App;
