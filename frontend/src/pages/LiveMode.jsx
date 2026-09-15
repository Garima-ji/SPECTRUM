/**
 * LiveMode.jsx
 * ────────────
 * Live audio fact-checking page.
 *
 * Architecture:
 *   MediaRecorder (Microphone OR System/Tab Audio)
 *     → allChunksRef   accumulates every 1-second slice
 *     → every 8 s: new Blob([...allChunksRef]) sent to POST /api/live/chunk
 *         (full blob = valid WebM with codec header always present)
 *     → backend transcribes async, deduplicates, extracts claims, verifies
 *   Every 2 s: GET /api/live/{sessionId} → update transcript + claims UI
 *
 * Nothing in this file touches the existing upload, transcript,
 * claims, or verify APIs.
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  AlertCircle,
  Circle,
  ExternalLink,
  HelpCircle,
  Loader2,
  MessageSquare,
  Mic,
  Square,
} from 'lucide-react';
import { liveApi } from '../services/liveApi';

// ── Constants ────────────────────────────────────────────────────────────────

/** How often the frontend sends a rolling audio window to the backend. */
const CHUNK_SEND_MS = 4_000;

/** Keep only the most recent N seconds of microphone slices (plus codec header). */
const MIC_WINDOW_SLICES = 12;

/** How often the frontend polls the backend for updated transcript/claims. */
const POLL_MS = 2_000;

// ── Style helpers (mirror Dashboard.jsx palette) ─────────────────────────────

const verdictStyles = {
  'PENDING': 'bg-slate-800/60 border-slate-700/60 text-slate-400',
  'SUPPORTED': 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400',
  'REFUTED':   'bg-red-500/10 border-red-500/30 text-red-400',
  'CONFLICTING': 'bg-amber-500/10 border-amber-500/30 text-amber-400',
  'INSUFFICIENT EVIDENCE': 'bg-slate-800/60 border-slate-700/60 text-slate-400',
};

const verdictEmoji = {
  'SUPPORTED': '🟢',
  'REFUTED':   '🔴',
  'CONFLICTING': '🟠',
  'INSUFFICIENT EVIDENCE': '⚪',
};

function getVerdictClass(v) {
  return verdictStyles[v] || verdictStyles['INSUFFICIENT EVIDENCE'];
}

function formatAudioTime(sec) {
  if (sec == null || Number.isNaN(sec)) return '00:00';
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function LiveMode() {
  // ── UI state ──────────────────────────────────────────────────────────────
  const [source, setSource]               = useState('microphone');
  const [streamUrl, setStreamUrl]         = useState('');
  const [status, setStatus]               = useState('idle');
  // idle | starting | live (capturing) | stopping | draining | complete
  const [uiError, setUiError]             = useState('');
  const [audioId, setAudioId]             = useState(null);
  const [transcriptSegs, setTranscriptSegs] = useState([]);
  const [claims, setClaims]               = useState([]);
  const [selectedClaim, setSelectedClaim] = useState(null);
  const [claimsProcessing, setClaimsProcessing] = useState(false); // Ollama extracting claims
  const [audioStatus, setAudioStatus]           = useState(''); // transcribing, silent
  const [pendingClaims, setPendingClaims]       = useState(0);

  // ── Stable refs (survive re-renders without triggering effects) ───────────
  const sessionIdRef      = useRef(null);   // backend session_id
  const mediaRecorderRef  = useRef(null);
  const streamRef         = useRef(null);
  const allChunksRef      = useRef([]);     // every 1-s MediaRecorder slice
  const chunkIndexRef     = useRef(0);
  const chunkTimerRef     = useRef(null);
  const pollTimerRef      = useRef(null);
  const transcriptPanelRef = useRef(null);  // ref to the transcript scroll container
  const stoppingRef       = useRef(false);  // guard against double-stop
  const uploadChainRef    = useRef(Promise.resolve()); // preserves final-chunk ordering

  // ── Scroll transcript panel only — never the whole page ─────────────────
  // Only auto-scroll if the user is already at (or very near) the bottom of
  // the panel.  If they have scrolled up to read earlier content, leave them.
  useEffect(() => {
    const panel = transcriptPanelRef.current;
    if (!panel || status !== 'live') return;
    const atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 80;
    if (atBottom) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, [transcriptSegs, status]);

  // ── Keep selectedClaim in sync with latest poll data ─────────────────────
  useEffect(() => {
    if (!selectedClaim) {
      if (claims.length > 0) setSelectedClaim(claims[0]);
      return;
    }
    const fresh = claims.find((c) => c.claim_id === selectedClaim.claim_id);
    if (fresh) setSelectedClaim(fresh);
  }, [claims]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cleanup on unmount ────────────────────────────────────────────────────
  useEffect(() => () => _teardownMedia(), []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Internal helpers ──────────────────────────────────────────────────────

  function _teardownMedia() {
    if (chunkTimerRef.current)  clearInterval(chunkTimerRef.current);
    if (pollTimerRef.current)   clearInterval(pollTimerRef.current);
    chunkTimerRef.current = null;
    pollTimerRef.current  = null;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try { mediaRecorderRef.current.stop(); } catch (_) { /* ignore */ }
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    allChunksRef.current = [];
    chunkIndexRef.current = 0;
    uploadChainRef.current = Promise.resolve();
  }

  /** Detect best supported audio MIME type. */
  function _getMimeType() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/ogg;codecs=opus',
      'audio/webm',
      'audio/mp4',
    ];
    for (const type of candidates) {
      if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return 'audio/webm'; // fallback
  }

  function _extFromMime(mimeType) {
    if (mimeType.includes('ogg')) return 'ogg';
    if (mimeType.includes('mp4')) return 'mp4';
    return 'webm';
  }

  /** Send continuous audio buffer to the backend without time gaps or splicing jumps. */
  function _windowedChunks(chunks) {
    return chunks;
  }

  async function _sendChunk(sid, mimeType) {
    if (allChunksRef.current.length === 0) {
      console.log('[Live] _sendChunk skipped — no chunks accumulated yet');
      return;
    }
    const windowed = _windowedChunks(allChunksRef.current);
    const blob = new Blob(windowed, { type: mimeType });
    if (blob.size < 512) {
      console.log('[Live] _sendChunk skipped — blob too small:', blob.size, 'bytes');
      return;            // too small to be useful
    }
    const idx = chunkIndexRef.current++;
    const ext = _extFromMime(mimeType);
    console.log(`[Live] uploading chunk = #${idx}, blob size = ${blob.size} bytes, ext = ${ext}`);
    // Chunk requests must remain ordered. Otherwise Stop can reach the API
    // before an in-flight final-audio upload and the backend correctly rejects
    // that late upload because capture is already closed.
    const upload = async () => {
      try {
        await liveApi.sendChunk(sid, blob, idx, ext);
      } catch (err) {
        console.warn('[LiveMode] sendChunk failed:', err);
      }
    };
    const chainedUpload = uploadChainRef.current.catch(() => undefined).then(upload);
    uploadChainRef.current = chainedUpload;
    return chainedUpload;
  }

  /** Poll backend and refresh transcript + claims. */
  async function _poll(sid) {
    try {
      const data = await liveApi.getSession(sid);
      setTranscriptSegs(data.transcript_segments || []);
      const newClaims = data.claims || [];
      setClaims((previousClaims) => {
        const previousById = new Map(previousClaims.map((claim) => [String(claim.claim_id), claim]));
        const merged = newClaims.map((claim) => {
          const previous = previousById.get(String(claim.claim_id));
          return previous
            ? { ...previous, ...claim, sources: claim.sources?.length ? claim.sources : (previous.sources || []) }
            : claim;
        });
        const returnedIds = new Set(merged.map((claim) => String(claim.claim_id)));
        return [
          ...merged,
          ...previousClaims.filter((claim) => !returnedIds.has(String(claim.claim_id))),
        ];
      });
      setPendingClaims(data.pending_claims ?? newClaims.filter((claim) => !['completed', 'failed'].includes(claim.verification_status)).length);
      setAudioStatus(data.audio_status || '');
      setClaimsProcessing(false);
      if (data.status === 'error') {
        setUiError(data.error || 'Live stream encountered an error.');
        setStatus('idle');
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        return;
      }
      if (data.status === 'complete') {
        setStatus('complete');
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        return;
      }
      if (data.status === 'draining') setStatus('draining');
    } catch (err) {
      console.warn('[LiveMode] poll failed:', err);
    }
  }

  // ── Status Helper ───────────────────────────────────────────────────────────
  function getLiveStatus() {
    if (status === 'starting') return source === 'stream' ? 'CONNECTING HLS...' : 'CONNECTING...';
    if (status === 'complete') return 'SESSION COMPLETE';
    if (status === 'draining' || status === 'stopping') {
      return pendingClaims > 0 ? `PROCESSING ${pendingClaims} REMAINING CLAIM${pendingClaims === 1 ? '' : 'S'}...` : 'FINALIZING SESSION...';
    }
    if (status !== 'live') return 'IDLE';
    
    // Reverse check (highest state first)
    if (claims.some(c => c.verification_status === 'completed' || c.verification_status === 'failed')) return 'VERDICT READY';
    if (claims.some(c => c.verification_status === 'verifying')) return 'VERIFYING CLAIM...';
    if (claims.some(c => c.verification_status === 'retrieving_evidence')) return 'RETRIEVING EVIDENCE...';
    if (claims.some(c => c.verification_status === 'search_completed')) return 'SEARCH COMPLETED...';
    if (claims.some(c => c.verification_status === 'evidence_validated')) return 'EVIDENCE VALIDATED...';
    if (claims.length > 0) return 'CLAIM DETECTED';
    if (claimsProcessing) return 'EXTRACTING CLAIMS...';
    
    if (audioStatus === 'transcribing') return 'TRANSCRIBING AUDIO...';
    if (audioStatus === 'silent') return 'AUDIO RECEIVED (SILENT / NO SPEECH)';
    if (status === 'live') return source === 'stream' ? 'HLS CONNECTED / WAITING FOR AUDIO...' : 'LISTENING...';
    
    return 'IDLE';
  }

  // ── Start ─────────────────────────────────────────────────────────────────

  const handleStart = useCallback(async () => {
    console.log('[Live] start clicked');
    setUiError('');
    setStatus('starting');
    setTranscriptSegs([]);
    setClaims([]);
    setSelectedClaim(null);
    stoppingRef.current = false;
    allChunksRef.current = [];
    chunkIndexRef.current = 0;
    uploadChainRef.current = Promise.resolve();
    setPendingClaims(0);

    // 1. Request media stream ------------------------------------------------
    let stream = null;
    if (source !== 'stream') {
      if (source === 'microphone') {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
          console.log('[Live] microphone acquired');
        } catch (err) {
          const msg =
            err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'
              ? 'Microphone permission was denied. Please allow microphone access in your browser settings and try again.'
              : err.name === 'NotFoundError'
              ? 'No microphone was found. Please connect a microphone and try again.'
              : `Could not access microphone: ${err.message}`;
          setUiError(msg);
          setStatus('idle');
          return;
        }
      } else if (source === 'system') {
        // System / tab audio via getDisplayMedia
        if (!navigator.mediaDevices?.getDisplayMedia) {
          setUiError(
            'System audio capture is not supported in this browser. ' +
            'Please use Google Chrome or Microsoft Edge on Windows or macOS.'
          );
          setStatus('idle');
          return;
        }
        try {
          stream = await navigator.mediaDevices.getDisplayMedia({
            audio: true,
            video: true,   // Most browsers require video to grant audio
          });
          const audioTracks = stream.getAudioTracks();
          if (audioTracks.length === 0) {
            stream.getTracks().forEach((t) => t.stop());
            setUiError(
              'No audio was included in the screen share. ' +
              'In the browser dialog, select a tab or window that has audio playing ' +
              'and make sure the "Share audio" or "Share tab audio" checkbox is checked.'
            );
            setStatus('idle');
            return;
          }
          // Drop video track — we only need audio
          stream.getVideoTracks().forEach((t) => t.stop());
        } catch (err) {
          const msg =
            err.name === 'NotAllowedError'
              ? 'Screen sharing was cancelled or denied. Please try again and select a tab or window with audio.'
              : err.name === 'NotSupportedError'
              ? 'System audio capture is not supported on this browser / OS combination.'
              : `Could not capture system audio: ${err.message}`;
          setUiError(msg);
          setStatus('idle');
          return;
        }
      }
      streamRef.current = stream;
    }

    // 2. Create backend session ---------------------------------------------
    let sessionId, newAudioId;
    try {
      if (source === 'stream') {
        console.log('[LIVE UI] Connect clicked');
        console.log('[LIVE UI] Input URL:', streamUrl);
        console.log('[LIVE UI] Input length:', streamUrl?.length);
        let cleanUrl = streamUrl ? streamUrl.trim() : '';
        if (!cleanUrl) {
          setUiError('Please enter a stream URL.');
          setStatus('idle');
          _teardownMedia();
          return;
        }
        try {
          const parsedUrl = new URL(cleanUrl);
          if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
            throw new Error('Invalid protocol');
          }
          cleanUrl = parsedUrl.href;
          console.log('[LIVE UI] Parsed URL:', cleanUrl);
        } catch (e) {
          setUiError('Please enter a valid HTTP/HTTPS stream URL.');
          setStatus('idle');
          _teardownMedia();
          return;
        }
        console.log('[LIVE UI] Sending request to:', cleanUrl);
        const res = await liveApi.startStream(cleanUrl);
        console.log('[LIVE UI] Backend response:', res);
        if (!res.success) {
          throw new Error(res.error || res.detail || 'Failed to start stream.');
        }
        sessionId = res.session_id;
        newAudioId = res.audio_id;
      } else {
        const res = await liveApi.startSession(source);
        sessionId  = res.session_id;
        newAudioId = res.audio_id;
      }
    } catch (err) {
      setUiError(`Failed to start backend session: ${err.message}`);
      setStatus('idle');
      _teardownMedia();
      return;
    }
    sessionIdRef.current = sessionId;
    setAudioId(newAudioId);
    
    // For streams, backend handles FFmpeg download. We just poll.
    if (source === 'stream') {
        setStatus('live');
        pollTimerRef.current = setInterval(() => {
          _poll(sessionIdRef.current);
        }, POLL_MS);
        return;
    }

    // 3. MediaRecorder setup ------------------------------------------------
    const mimeType = _getMimeType();
    console.log('[Live] MediaRecorder MIME type =', mimeType);
    let recorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType });
    } catch (err) {
      // Try without explicit mimeType if the browser rejected it
      try { recorder = new MediaRecorder(stream); }
      catch (err2) {
        setUiError(`Could not create MediaRecorder: ${err2.message}`);
        setStatus('idle');
        _teardownMedia();
        return;
      }
    }
    mediaRecorderRef.current = recorder;
    const activeMime = recorder.mimeType || mimeType;
    console.log('[Live] MediaRecorder effective MIME =', activeMime);

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        console.log('[Live] chunk received size =', event.data.size, 'bytes');
        allChunksRef.current.push(event.data);
      }
    };

    recorder.onerror = (event) => {
      console.error('[LiveMode] MediaRecorder error:', event.error);
      setUiError(`Recording error: ${event.error?.message || 'Unknown error'}. Please stop and try again.`);
    };

    // When the audio track ends (user stops sharing / mic unplugged)
    stream.getAudioTracks().forEach((track) => {
      track.onended = () => {
        console.log('[LiveMode] Audio track ended — auto-stopping.');
        handleStop();
      };
    });

    // Start collecting 1-second slices
    recorder.start(1_000);
    console.log('[Live] MediaRecorder started (timeslice=1000ms, chunk every', CHUNK_SEND_MS, 'ms)');
    setStatus('live');

    // 4. Send accumulated audio every CHUNK_SEND_MS -------------------------
    chunkTimerRef.current = setInterval(() => {
      _sendChunk(sessionIdRef.current, activeMime);
    }, CHUNK_SEND_MS);

    // 5. Poll backend every POLL_MS ----------------------------------------
    pollTimerRef.current = setInterval(() => {
      _poll(sessionIdRef.current);
    }, POLL_MS);

  }, [source, streamUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Stop ──────────────────────────────────────────────────────────────────

  const handleStop = useCallback(async () => {
    if (stoppingRef.current) return;
    stoppingRef.current = true;

    const sid = sessionIdRef.current;
    setStatus('stopping');

    // Stop producing new audio, but keep polling while the backend drains.
    clearInterval(chunkTimerRef.current);
    chunkTimerRef.current = null;

    // Stop the recorder first. Its stop event emits the final buffered slice;
    // wait for that event before snapshotting/sending the full WebM blob.
    if (mediaRecorderRef.current?.state === 'recording') {
      const recorder = mediaRecorderRef.current;
      await new Promise((resolve) => {
        const timeout = setTimeout(resolve, 1_500);
        recorder.addEventListener('stop', () => {
          clearTimeout(timeout);
          resolve();
        }, { once: true });
        try { recorder.stop(); } catch (_) {
          clearTimeout(timeout);
          resolve();
        }
      });
    }

    // The final upload is queued behind every earlier upload. /stop is called
    // only after it resolves, so the backend sees all accepted microphone audio
    // before transitioning CAPTURING -> DRAINING.
    if (sid && allChunksRef.current.length > 0) {
      const mimeType = mediaRecorderRef.current?.mimeType || 'audio/webm';
      await _sendChunk(sid, mimeType);
    }

    // Tear down the capture device only; polling remains alive while workers drain.
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

    // Notify backend
    if (sid) {
      try { await liveApi.stopSession(sid); } catch (_) { /* ignore */ }
      setStatus('draining');
      if (!pollTimerRef.current) {
        pollTimerRef.current = setInterval(() => _poll(sid), POLL_MS);
      }
      try { await _poll(sid); } catch (_) { /* ignore */ }
    }

    setStatus((current) => current === 'complete' ? current : 'draining');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Render ────────────────────────────────────────────────────────────────

  const isActive  = status === 'live';
  const isDraining = status === 'draining' || status === 'stopping';
  const isComplete = status === 'complete';
  const showPanels = isActive || isDraining || isComplete;

  return (
    <div className="space-y-6">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-white">Live Mode</h1>

            {isActive && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-red-500/15 border border-red-500/30 rounded-full">
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                <span className="text-[11px] font-bold text-red-400 tracking-widest">LIVE</span>
              </div>
            )}
            {isDraining && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-amber-500/10 border border-amber-500/30 rounded-full">
                <span className="text-[11px] font-bold text-amber-300 tracking-widest">
                  {source === 'stream' ? 'STREAM CAPTURE STOPPED' : 'MICROPHONE STOPPED'}
                </span>
              </div>
            )}
            {isComplete && (
              <span className="text-xs font-semibold text-emerald-400">
                ✓ Session Complete
                {audioId && <span className="ml-2 text-slate-500 font-normal">· Audio ID {audioId} saved to Audit Log</span>}
              </span>
            )}
          </div>
          <p className="text-sm text-slate-400">
            Real-time transcription and fact-checking during live speech
          </p>
        </div>

        {isActive && (
          <button
            id="live-stop-btn"
            onClick={handleStop}
            disabled={false}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-red-400 text-sm font-semibold rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <><Square className="h-4 w-4 fill-current" /> {source === 'stream' ? 'Stop Stream Capture' : 'Stop Microphone'}</>
          </button>
        )}
      </div>

      {/* ── Error banner ────────────────────────────────────────────────── */}
      {uiError && (
        <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/20 rounded-xl">
          <AlertCircle className="h-5 w-5 text-red-400 flex-none mt-0.5" />
          <p className="text-xs text-slate-300 flex-1">{uiError}</p>
          <button
            onClick={() => setUiError('')}
            className="text-slate-500 hover:text-slate-300 text-xs font-bold flex-none"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Source selector + Start (idle / starting) ───────────────────── */}
      {(status === 'idle' || status === 'starting') && (
        <div className="glass p-8 rounded-3xl space-y-8 max-w-xl mx-auto shadow-2xl relative overflow-hidden">
          {/* Ambient glow */}
          <div className="absolute -top-20 -left-20 w-44 h-44 bg-red-500/5 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute -bottom-20 -right-20 w-44 h-44 bg-brand-500/5 rounded-full blur-3xl pointer-events-none" />

          {/* Source selector */}
          <div className="space-y-4">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
              Input Source
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {[
                { id: 'microphone', label: 'Microphone', sub: 'In-room speech', Icon: Mic },
                { id: 'stream',     label: 'Live Stream URL', sub: 'URL', Icon: ExternalLink },
              ].map(({ id, label, sub, Icon }) => (
                <button
                  key={id}
                  id={`live-source-${id}`}
                  onClick={() => setSource(id)}
                  className={`flex flex-col items-center gap-3 p-5 rounded-2xl border-2 transition-all duration-200 ${
                    source === id
                      ? 'border-brand-500/60 bg-brand-500/10 text-brand-300'
                      : 'border-slate-800 bg-slate-900/40 text-slate-400 hover:border-slate-700 hover:text-slate-300'
                  }`}
                >
                  <Icon className={`h-7 w-7 ${source === id ? 'text-brand-400' : 'text-slate-500'}`} />
                  <div className="text-center">
                    <p className="text-sm font-semibold">{label}</p>
                    <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>
                  </div>
                </button>
              ))}
            </div>

            {/* Stream URL Input */}
            {source === 'stream' && (
              <div className="p-4 bg-slate-900/40 border border-slate-800 rounded-xl space-y-3">
                <label className="text-[11px] font-bold text-slate-400 uppercase">Stream URL</label>
                <input
                  type="text"
                  placeholder="Enter a public HTTP/HTTPS media stream URL"
                  value={streamUrl}
                  onChange={(e) => setStreamUrl(e.target.value)}
                  className="w-full bg-slate-800 border-none rounded-lg text-sm px-4 py-3 text-white focus:ring-2 focus:ring-brand-500 outline-none"
                />
                <p className="text-[10px] text-slate-500 leading-relaxed">
                  Supports publicly accessible media streams such as HLS (.m3u8) and other FFmpeg-compatible streams.
                </p>
              </div>
            )}
          </div>

          {/* Start button */}
          <div className="space-y-2">
            <button
              id="live-start-btn"
              onClick={handleStart}
              disabled={status === 'starting'}
              className="w-full flex items-center justify-center gap-2.5 py-3.5 bg-red-500 hover:bg-red-600 active:bg-red-700 text-white font-bold text-sm rounded-2xl transition-all shadow-lg shadow-red-500/20 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {status === 'starting' ? (
                <><Loader2 className="h-5 w-5 animate-spin" /> {source === 'stream' ? 'Connecting Stream…' : 'Starting Live Session…'}</>
              ) : (
                <><Circle className="h-4 w-4 fill-current" /> {source === 'stream' ? 'Connect Stream' : 'Start Live Fact Check'}</>
              )}
            </button>
            <p className="text-[10px] text-slate-600 text-center">
              Audio is processed on-device via Whisper (CPU). First transcript appears after ~15–30 s.
            </p>
          </div>
        </div>
      )}

      {/* ── Live / Stopped — two-panel view ─────────────────────────────── */}
      {showPanels && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* ── LEFT: Live Transcript ─────────────────────────────────── */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">
                Live Transcript
              </h3>
              {(isActive || isDraining || isComplete) && (
                <span className="flex items-center gap-1.5 text-[10px] text-brand-400 font-bold uppercase tracking-wider bg-brand-500/10 px-2 py-1 rounded">
                  {['IDLE', 'VERDICT READY', 'SESSION COMPLETE'].includes(getLiveStatus()) ? (
                    <Circle className="h-2 w-2 fill-current" />
                  ) : (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  )}
                  {getLiveStatus()}
                </span>
              )}
            </div>

            <div
              ref={transcriptPanelRef}
              className="glass p-4 rounded-2xl h-[520px] overflow-y-auto space-y-4 border-slate-800/60"
            >
              {transcriptSegs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full space-y-3 text-center">
                  {isActive || isDraining ? (
                    <>
                      <Loader2 className="h-8 w-8 text-brand-400 animate-spin" />
                      <div className="space-y-1">
                        <p className="text-sm font-bold text-brand-300">{getLiveStatus()}</p>
                        <p className="text-xs text-slate-500 max-w-xs">
                          Transcript will appear when speech is detected.
                          Waiting for background processing (typically 10–20s on CPU).
                        </p>
                      </div>
                    </>
                  ) : (
                    <>
                      <MessageSquare className="h-8 w-8 text-slate-600" />
                      <p className="text-xs text-slate-500">
                        No speech was detected in this session.
                      </p>
                    </>
                  )}
                </div>
              ) : (
                <>
                  {transcriptSegs.map((seg, idx) => (
                    <div key={idx} className="space-y-1">
                      <span className="text-[10px] text-slate-600 font-mono leading-none">
                        {formatAudioTime(seg.start)}
                      </span>
                      <p className="text-xs text-slate-200 leading-relaxed">
                        &ldquo;{seg.text}&rdquo;
                      </p>
                    </div>
                  ))}
                </>
              )}
            </div>
          </section>

          {/* ── RIGHT: Live Fact Check ────────────────────────────────── */}
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">
                Live Fact Check
              </h3>
              {isDraining && (
                <span className="text-[10px] font-semibold text-amber-300 text-right">
                  Processing remaining claims{pendingClaims > 0 ? ` · ${pendingClaims} pending` : ''}
                </span>
              )}
            </div>

            <div className="space-y-3 h-[520px] overflow-y-auto pr-1">
              {claims.length === 0 ? (
                <div className="glass p-8 text-center rounded-2xl h-full flex flex-col items-center justify-center space-y-3">
                  {isActive ? (
                    <>
                      <HelpCircle className="h-8 w-8 text-slate-600" />
                      <p className="text-xs font-semibold text-slate-400 max-w-xs">
                        Claims will appear once factual statements are detected in the live transcript.
                      </p>
                    </>
                  ) : isDraining ? (
                    <>
                      <Loader2 className="h-8 w-8 text-amber-300 animate-spin" />
                      <p className="text-xs font-semibold text-slate-400 max-w-xs">
                        {source === 'stream' ? 'Stream capture' : 'Microphone capture'} has stopped. Final audio and any remaining claims are still being processed.
                      </p>
                    </>
                  ) : (
                    <>
                      <HelpCircle className="h-8 w-8 text-slate-600" />
                      <p className="text-xs font-semibold text-slate-400 max-w-xs">
                        No checkable claims were detected in this session.
                      </p>
                    </>
                  )}
                </div>
              ) : (
                claims.map((claim) => {
                  const checking = ['claim_detected', 'retrieving_evidence', 'search_completed', 'evidence_validated', 'verifying'].includes(claim.verification_status);
                  const failed = claim.verification_status === 'failed';
                  const selected = selectedClaim?.claim_id === claim.claim_id;
                  return (
                    <button
                      key={claim.claim_id}
                      onClick={() => setSelectedClaim(claim)}
                      className={`glass w-full p-4 rounded-xl text-left border-l-4 transition-all ${
                        selected
                          ? 'border-l-brand-500 bg-brand-500/5'
                          : 'border-l-slate-700 hover:border-l-slate-600'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-xs font-semibold text-slate-200 leading-relaxed flex-1">
                          {claim.claim_text}
                        </p>
                        <span className="text-base flex-none">
                          {checking ? '⏳' : (verdictEmoji[claim.verdict] || '⚪')}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-2.5 flex-wrap">
                        <span className="text-[10px] text-slate-500">
                          {claim.speaker || 'Speaker Unknown'}
                        </span>
                        {claim.timestamp && (
                          <>
                            <span className="text-[10px] text-slate-700">·</span>
                            <span className="text-[10px] text-slate-600 font-mono">{claim.timestamp}</span>
                          </>
                        )}
                        <span className="ml-auto">
                          {checking ? (
                            <span className="text-[10px] text-blue-400 font-semibold flex items-center gap-1">
                              <Loader2 className="h-3 w-3 animate-spin" /> {claim.status || 'Checking…'}
                            </span>
                          ) : failed ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded font-bold border bg-amber-500/10 border-amber-500/30 text-amber-300">
                              {claim.status || 'Verification Error'}
                            </span>
                          ) : (
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold border ${getVerdictClass(claim.verdict)}`}>
                              {claim.verdict}
                            </span>
                          )}
                        </span>
                      </div>
                      {claim.sources?.length > 0 && (
                        <p className="text-[10px] text-slate-500 mt-2">
                          Evidence found: {claim.sources.length}
                        </p>
                      )}
                    </button>
                  );
                })
              )}
            </div>
          </section>
        </div>
      )}

      {/* ── Claim Detail Panel ───────────────────────────────────────────── */}
      {selectedClaim && showPanels && (
        <div className="glass p-6 rounded-2xl space-y-5 border-slate-800/80 shadow-xl">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">
              Claim Detail
            </h3>
            <button
              onClick={() => setSelectedClaim(null)}
              className="text-slate-500 hover:text-slate-300 text-sm font-bold"
            >
              ✕
            </button>
          </div>

          {/* Statement */}
          <div>
            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">
              Statement
            </span>
            <p className="text-xs text-slate-200 font-medium leading-relaxed bg-slate-900/40 p-3.5 rounded-xl mt-1.5">
              {selectedClaim.claim_text}
            </p>
          </div>

          {/* Metadata grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className={`p-3 rounded-xl border ${getVerdictClass(selectedClaim.verdict)}`}>
              <span className="text-[9px] uppercase font-bold opacity-60">Verdict</span>
              <p className="text-sm font-extrabold mt-1">
                {['claim_detected', 'retrieving_evidence', 'search_completed', 'evidence_validated', 'verifying'].includes(selectedClaim.verification_status)
                  ? (selectedClaim.status || 'Checking…')
                  : selectedClaim.verification_status === 'failed'
                  ? (selectedClaim.status || 'Verification Error')
                  : selectedClaim.verdict}
              </p>
            </div>
            <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-200">
              <span className="text-[9px] uppercase font-bold opacity-60">Confidence</span>
              <p className="text-sm font-extrabold mt-1">
                {typeof selectedClaim.confidence === 'number'
                  ? `${Math.round(selectedClaim.confidence <= 1 ? selectedClaim.confidence * 100 : selectedClaim.confidence)}%`
                  : '—'}
              </p>
            </div>
            <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-200">
              <span className="text-[9px] uppercase font-bold opacity-60">Status</span>
              <p className="text-sm font-extrabold mt-1 uppercase">
                {selectedClaim.verification_status}
              </p>
            </div>
          </div>
          
          {/* Confidence Factors Grid */}
          {selectedClaim.confidence_factors && (
            <div className="p-4 rounded-xl bg-slate-900/40 border border-slate-800 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">
                  Confidence Assessment
                </span>
                <span className="text-[10px] text-slate-400 font-mono">
                  Multi-Factor Explainability
                </span>
              </div>
              {(() => {
                let factors = {};
                try {
                  factors = typeof selectedClaim.confidence_factors === 'string'
                    ? JSON.parse(selectedClaim.confidence_factors)
                    : selectedClaim.confidence_factors;
                } catch (e) {}
                const fmtPct = (v) => (v != null && !isNaN(v) ? `${Math.round(v)}%` : '—');
                const fmtInt = (v) => (v != null && !isNaN(v) ? String(v) : '—');

                return (
                  <div className="space-y-3">
                    {factors.explanation && (
                      <p className="text-xs text-brand-300 font-medium bg-brand-500/10 p-2.5 rounded-lg border border-brand-500/20 leading-relaxed">
                        {factors.explanation}
                      </p>
                    )}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                      {/* Section 1: Evidence */}
                      <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                        <span className="text-[9px] uppercase font-bold text-slate-400 tracking-wider">Evidence Assessment</span>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Evidence Strength:</span><span className="font-semibold text-slate-200">{fmtPct(factors.evidence_strength)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Evidence Coverage:</span><span className="font-semibold text-slate-200">{fmtPct(factors.evidence_coverage)}</span></div>
                      </div>

                      {/* Section 2: Sources */}
                      <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                        <span className="text-[9px] uppercase font-bold text-slate-400 tracking-wider">Source Credibility</span>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Source Quality:</span><span className="font-semibold text-slate-200">{fmtPct(factors.source_quality)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Source Consistency:</span><span className="font-semibold text-slate-200">{fmtPct(factors.source_consistency)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Source Diversity:</span><span className="font-semibold text-slate-200">{fmtPct(factors.source_diversity)}</span></div>
                      </div>

                      {/* Section 3: Claim & Context */}
                      <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                        <span className="text-[9px] uppercase font-bold text-slate-400 tracking-wider">Claim & Context</span>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Claim Clarity:</span><span className="font-semibold text-slate-200">{fmtPct(factors.claim_clarity)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Temporal Relevance:</span><span className="font-semibold text-slate-200">{fmtPct(factors.temporal_relevance)}</span></div>
                      </div>

                      {/* Section 4: Source Breakdown */}
                      <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                        <span className="text-[9px] uppercase font-bold text-slate-400 tracking-wider">Source Breakdown</span>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Supporting:</span><span className="font-semibold text-emerald-400">{fmtInt(factors.supporting_count)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Contradicting:</span><span className="font-semibold text-rose-400">{fmtInt(factors.contradicting_count)}</span></div>
                        <div className="flex justify-between text-[11px]"><span className="text-slate-500">Total Sources:</span><span className="font-semibold text-slate-200">{fmtInt(factors.total_source_count)}</span></div>
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {/* Reasoning */}
          {selectedClaim.reasoning && (
            <div className="space-y-1">
              <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">
                Reasoning
              </span>
              <p className="text-xs text-slate-400 leading-relaxed">
                {selectedClaim.reasoning}
              </p>
            </div>
          )}

          {/* Sources */}
          {selectedClaim.sources?.length > 0 && (
            <div className="space-y-2">
              <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">
                Supporting Evidence
              </span>
              {selectedClaim.sources.map((src, i) => (
                <a
                  key={i}
                  href={src.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block p-3 bg-slate-900/30 hover:bg-slate-900/60 border border-slate-800 rounded-xl transition"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[10px] font-semibold text-slate-300 truncate">
                      {src.title}
                    </p>
                    <ExternalLink className="h-3.5 w-3.5 text-brand-400 flex-none" />
                  </div>
                  {src.snippet && (
                    <p className="text-[10px] text-slate-500 mt-1.5 leading-relaxed">
                      {src.snippet}
                    </p>
                  )}
                </a>
              ))}
            </div>
          )}

          {/* No sources message */}
          {selectedClaim.verification_status === 'completed' &&
            (!selectedClaim.sources || selectedClaim.sources.length === 0) && (
              <p className="text-xs text-slate-500">
                Insufficient evidence was retrieved for this claim.
              </p>
            )}
        </div>
      )}
    </div>
  );
}
