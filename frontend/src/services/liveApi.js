/**
 * liveApi.js
 * ──────────
 * Thin Axios wrapper for the Live Mode backend endpoints.
 *
 * Intentionally separate from api.js so the existing upload pipeline
 * is completely undisturbed.
 *
 * FIX: Do NOT manually set Content-Type for multipart/form-data.
 * Axios/browser must set it automatically so the multipart boundary
 * token is included.  A manually-set 'multipart/form-data' header
 * without the boundary causes FastAPI to reject the request with 422.
 */

import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const client = axios.create({ baseURL: API_URL });

export const liveApi = {
  /**
   * Create a new live session on the backend.
   * @param {string} source  "microphone" | "system"
   * @returns {{ session_id: string, audio_id: number }}
   */
  startSession: async (source) => {
    console.log('[Live] POST /api/live/start — source:', source);
    const res = await client.post('/live/start', { source });
    console.log('[Live] /api/live/start response:', res.data);
    return res.data;
  },

  /**
   * Start a live session from a stream URL.
   */
  startStream: async (url) => {
    console.log('[Live] POST /api/live/stream — url:', url);
    try {
      const res = await client.post('/live/stream', { url });
      return { success: true, ...res.data };
    } catch (err) {
      if (err.response && err.response.data) {
        return { success: false, ...err.response.data };
      }
      throw err;
    }
  },

  /**
   * Send the full accumulated audio blob to the backend for transcription.
   *
   * The blob MUST be built by the caller as:
   *   new Blob([...allCollectedChunks], { type: mimeType })
   * This ensures the WebM/Opus codec init header is always present.
   *
   * IMPORTANT: Do NOT pass a Content-Type header here.  The browser must
   * generate the multipart boundary automatically.  Manually setting
   * 'Content-Type: multipart/form-data' without a boundary string causes
   * FastAPI to respond 422 Unprocessable Entity and the network panel shows
   * zero successful chunk requests.
   *
   * @param {string} sessionId
   * @param {Blob}   audioBlob   — full accumulated audio, not just the latest slice
   * @param {number} chunkIndex  — monotonically increasing counter
   * @param {string} ext         — file extension hint: "webm" | "ogg" | "mp4"
   */
  sendChunk: async (sessionId, audioBlob, chunkIndex, ext = 'webm') => {
    console.log(`[Live] uploading chunk #${chunkIndex} — ${audioBlob.size} bytes`);
    const form = new FormData();
    form.append('session_id', sessionId);
    form.append('chunk_index', String(chunkIndex));
    form.append('audio', audioBlob, `chunk_${chunkIndex}.${ext}`);

    // ⚠️  NO explicit Content-Type header — browser sets it with the boundary.
    const res = await client.post('/live/chunk', form, {
      timeout: 30_000,
    });
    console.log(`[Live] chunk upload response #${chunkIndex}:`, res.data);
    return res.data;
  },

  /**
   * Poll the current state of a live session.
   * Returns the transcript segments + claims as they accumulate.
   * @param {string} sessionId
   */
  getSession: async (sessionId) => {
    console.log('[Live] polling session =', sessionId);
    const res = await client.get(`/live/${sessionId}`);
    return res.data;
  },

  /**
   * Stop a live session and finalise the AudioRecord in the DB.
   * @param {string} sessionId
   */
  stopSession: async (sessionId) => {
    console.log('[Live] POST /api/live/stop — session:', sessionId);
    const res = await client.post('/live/stop', { session_id: sessionId });
    return res.data;
  },
};
