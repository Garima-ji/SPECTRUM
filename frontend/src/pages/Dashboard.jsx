import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle, ArrowRight, Clock, ExternalLink, FileAudio, FileText,
  HelpCircle, RefreshCw, ShieldCheck,
} from 'lucide-react';
import api from '../services/api';

const verdictStyles = {
  'SUPPORTED': 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400',
  'REFUTED': 'bg-red-500/10 border-red-500/30 text-red-400',
  'CONFLICTING': 'bg-amber-500/10 border-amber-500/30 text-amber-400',
  'INSUFFICIENT EVIDENCE': 'bg-slate-800/60 border-slate-700/60 text-slate-400',
};

const implementationStyles = {
  Completed: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25',
  'In Progress': 'bg-brand-500/10 text-brand-300 border-brand-500/25',
  Delayed: 'bg-amber-500/10 text-amber-400 border-amber-500/25',
  'Not Started': 'bg-slate-800 text-slate-400 border-slate-700',
  'No Reliable Update': 'bg-slate-900/60 text-slate-500 border-slate-700',
};

/** Format seconds (float) as MM:SS */
function formatDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return null;
  const s = Math.max(0, Math.round(seconds));
  const mm = Math.floor(s / 60).toString().padStart(2, '0');
  const ss = (s % 60).toString().padStart(2, '0');
  return `${mm}:${ss}`;
}

export default function Dashboard({ audioId, onBackToHistory }) {
  const [stats, setStats] = useState(null);
  const [audioRecord, setAudioRecord] = useState(null);
  const [claims, setClaims] = useState([]);
  const [selectedClaim, setSelectedClaim] = useState(null);
  const [isVerifying, setIsVerifying] = useState({});
  const pollInterval = useRef(null);

  const fetchStats = useCallback(async () => {
    try {
      setStats(await api.getDashboardStats());
    } catch (error) {
      console.error('Failed to load statistics:', error);
    }
  }, []);

  const fetchAudioDetails = useCallback(async (id) => {
    if (!id) return;
    try {
      const [record, claimsResponse] = await Promise.all([api.getTranscript(id), api.getClaims(id)]);
      setAudioRecord(record);
      setClaims(claimsResponse.claims);
      setSelectedClaim((current) => {
        if (!current) return claimsResponse.claims[0] || null;
        return claimsResponse.claims.find((claim) => claim.id === current.id) || claimsResponse.claims[0] || null;
      });
      // Stop polling once the pipeline is fully done (completed or failed).
      // 'processing' = Whisper running, 'transcribed' = Ollama running — keep polling.
      const done = record.status === 'completed' || record.status === 'failed';
      if (done && pollInterval.current) {
        clearInterval(pollInterval.current);
        pollInterval.current = null;
      }
    } catch (error) {
      console.error('Failed to load audio details:', error);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    if (!audioId) return undefined;

    fetchAudioDetails(audioId);
    // Poll every 5 seconds (reduced from 3s) to avoid hammering the backend.
    pollInterval.current = setInterval(() => {
      fetchAudioDetails(audioId);
      fetchStats();
    }, 5000);

    return () => {
      if (pollInterval.current) clearInterval(pollInterval.current);
      pollInterval.current = null;
    };
  }, [audioId, fetchAudioDetails, fetchStats]);

  const handleManualReverify = async (claimId) => {
    setIsVerifying((current) => ({ ...current, [claimId]: true }));
    try {
      await api.verifyClaim(claimId);
      await fetchAudioDetails(audioId);
      setTimeout(() => fetchAudioDetails(audioId), 3000);
    } catch (error) {
      console.error('Manual verification failed:', error);
    } finally {
      setIsVerifying((current) => ({ ...current, [claimId]: false }));
    }
  };

  const confidenceLabel = (confidence) => (
    typeof confidence === 'number' ? `${Math.round(confidence)}%` : 'Not available'
  );

  const verdictClass = (verdict) => verdictStyles[verdict] || verdictStyles['INSUFFICIENT EVIDENCE'];

  // Determine if claim extraction failed while transcript succeeded
  const claimExtractionFailed =
    audioRecord?.status === 'failed' &&
    audioRecord?.transcript_text &&
    claims.length === 0;

  return (
    <div className="space-y-8">
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {[
            [FileAudio, 'Audited Audios', stats.total_audio, 'text-brand-400'],
            [FileText, 'Claims Extracted', stats.total_claims, 'text-indigo-400'],
            [ShieldCheck, 'Truth Rate', `${stats.truth_rate}%`, 'text-emerald-400'],
            [Clock, 'Verified Items', stats.verified_claims_count, 'text-amber-400'],
          ].map(([Icon, label, value, color]) => (
            <div key={label} className="glass p-5 rounded-2xl flex items-center space-x-4">
              <div className={`p-3 bg-slate-900 rounded-xl border border-slate-800 ${color}`}><Icon className="h-5 w-5" /></div>
              <div><p className="text-[10px] uppercase font-bold tracking-wider text-slate-500">{label}</p><h3 className="text-xl font-bold text-white mt-0.5">{value}</h3></div>
            </div>
          ))}
        </div>
      )}

      {audioRecord && (
        <>
          <div className="glass p-6 rounded-2xl flex flex-col md:flex-row md:items-center justify-between border-slate-800 gap-4">
            <div className="space-y-1">
              <div className="flex items-center space-x-3 flex-wrap gap-y-2">
                <h2 className="text-lg font-bold text-white">{audioRecord.filename}</h2>
                {audioRecord.status === 'processing' && <span className="bg-brand-500/10 text-brand-400 px-2 py-0.5 rounded text-[10px] font-bold border border-brand-500/20">TRANSCRIBING</span>}
                {audioRecord.status === 'transcribed' && <span className="bg-indigo-500/10 text-indigo-400 px-2 py-0.5 rounded text-[10px] font-bold border border-indigo-500/20">EXTRACTING CLAIMS</span>}
                {audioRecord.status === 'claims_extracted' && <span className="bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded text-[10px] font-bold border border-amber-500/20">VERIFYING CLAIMS</span>}
                {audioRecord.status === 'completed' && <span className="bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded text-[10px] font-bold border border-emerald-500/20">COMPLETED</span>}
                {audioRecord.status === 'failed' && audioRecord.transcript_text && (
                  <span className="bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded text-[10px] font-bold border border-amber-500/20">PARTIAL — CLAIM EXTRACTION FAILED</span>
                )}
                {audioRecord.status === 'failed' && !audioRecord.transcript_text && (
                  <span className="bg-red-500/10 text-red-400 px-2 py-0.5 rounded text-[10px] font-bold border border-red-500/20">FAILED</span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                Uploaded {new Date(audioRecord.created_at).toLocaleString()}
                {audioRecord.duration != null && (
                  <span> · Audio duration: <span className="text-slate-300 font-semibold">{formatDuration(audioRecord.duration)}</span> ({audioRecord.duration.toFixed(2)}s)</span>
                )}
              </p>
            </div>
            <div className="flex items-center space-x-3">
              <button onClick={() => fetchAudioDetails(audioRecord.audio_id || audioId)} className="px-4 py-2 border border-slate-800 bg-slate-900 text-xs font-semibold text-slate-300 rounded-xl flex items-center space-x-1.5"><RefreshCw className="h-3.5 w-3.5" /><span>Refresh</span></button>
              <button onClick={onBackToHistory} className="px-4 py-2 bg-slate-800 text-xs font-semibold text-slate-200 rounded-xl flex items-center space-x-1"><span>Audit Log</span><ArrowRight className="h-3.5 w-3.5" /></button>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
            {/* ── Transcript ─────────────────────────────────────────── */}
            <section className="lg:col-span-4 space-y-4">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Speech Transcript</h3>
              <div className="glass p-5 rounded-2xl h-[480px] overflow-y-auto text-sm text-slate-300 leading-relaxed border-slate-800/60">
                {audioRecord.transcript_text ? (
                  <p className="font-light whitespace-pre-wrap">{audioRecord.transcript_text}</p>
                ) : audioRecord.status === 'processing' ? (
                  <div className="flex flex-col items-center justify-center h-full space-y-3 text-center"><RefreshCw className="h-7 w-7 text-brand-400 animate-spin" /><p className="text-xs text-slate-500">Transcription is running…</p></div>
                ) : audioRecord.status === 'failed' && !audioRecord.transcript_text ? (
                  <div className="text-xs text-red-400">{audioRecord.error_message || 'Transcription failed for this audio.'}</div>
                ) : (
                  <div className="text-xs text-slate-500">No transcript was produced for this audio.</div>
                )}
              </div>
            </section>

            {/* ── Extracted Claims ───────────────────────────────────── */}
            <section className="lg:col-span-4 space-y-4">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Extracted Claims</h3>
              <div className="space-y-3 h-[480px] overflow-y-auto pr-1">
                {claims.length ? claims.map((claim) => (
                  <button key={claim.id} type="button" onClick={() => setSelectedClaim(claim)} className={`glass w-full p-4 rounded-xl text-left border-l-4 ${selectedClaim?.id === claim.id ? 'border-l-brand-500 bg-brand-500/5' : 'border-l-slate-700'}`}>
                    <p className="text-xs font-semibold text-slate-200 leading-relaxed">{claim.claim_text}</p>
                    <div className="flex items-center justify-between mt-3 text-[10px] gap-2"><span className="text-slate-500">{claim.speaker || 'Speaker not identified'} · {claim.timestamp || 'Timestamp unavailable'}</span><span className={`px-2 py-0.5 rounded font-bold ${verdictClass(claim.verdict)}`}>{claim.verdict}</span></div>
                  </button>
                )) : (
                  <div className="glass p-8 text-center rounded-2xl h-full flex flex-col items-center justify-center space-y-2">
                    <AlertCircle className="h-8 w-8 text-slate-600" />
                    {claimExtractionFailed ? (
                      <>
                        <p className="text-xs font-semibold text-amber-400">Claim extraction failed.</p>
                        <p className="text-[10px] text-slate-500 max-w-xs">{audioRecord.error_message || 'Ollama could not extract claims from this transcript.'}</p>
                      </>
                    ) : (
                      <p className="text-xs font-semibold text-slate-400">
                        {audioRecord.status === 'processing'
                          ? 'Transcription is running…'
                          : audioRecord.status === 'transcribed'
                          ? 'Claim extraction is running…'
                          : audioRecord.status === 'claims_extracted'
                          ? 'Verification is running…'
                          : 'No factual claims were identified.'}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </section>

            {/* ── Analysis & Verdict ─────────────────────────────────── */}
            <section className="lg:col-span-4 space-y-4">
              <h3 className="text-sm font-bold text-slate-300 uppercase tracking-wider">Analysis & Verdict</h3>
              {selectedClaim ? (
                <div className="glass p-6 rounded-2xl h-[480px] overflow-y-auto space-y-6 shadow-xl border-slate-800/80">
                  <div><span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Statement</span><p className="text-xs text-slate-200 font-medium leading-relaxed bg-slate-900/40 p-3.5 rounded-xl mt-1.5">{selectedClaim.claim_text}</p></div>
                  <div className="grid grid-cols-2 gap-3 text-left">
                    <div className={`p-3 rounded-xl border ${verdictClass(selectedClaim.verdict)}`}><span className="text-[9px] uppercase font-bold opacity-60">Verdict</span><p className="text-sm font-extrabold mt-1">{selectedClaim.verdict}</p></div>
                    <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-200"><span className="text-[9px] uppercase font-bold opacity-60">Confidence</span><p className="text-sm font-extrabold mt-1">{confidenceLabel(selectedClaim.confidence)}</p></div>
                    <div className="p-3 rounded-xl border border-slate-700 bg-slate-900/60 text-slate-200"><span className="text-[9px] uppercase font-bold opacity-60">Verification</span><p className="text-sm font-extrabold mt-1 uppercase">{selectedClaim.verification_status}</p></div>
                    <div className={`p-3 rounded-xl border ${implementationStyles[selectedClaim.status] || implementationStyles['No Reliable Update']}`}><span className="text-[9px] uppercase font-bold opacity-60">Implementation</span><p className="text-sm font-extrabold mt-1">{selectedClaim.status}</p></div>
                  </div>
                  <div className="space-y-2"><span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Reasoning</span><p className="text-xs text-slate-400 leading-relaxed">{selectedClaim.reasoning || `Verification status: ${selectedClaim.verification_status}.`}</p></div>
                  {selectedClaim.confidence_factors && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Confidence Assessment</span>
                        <span className="text-[10px] text-slate-400 font-mono">Multi-Factor Explainability</span>
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
                  <div className="space-y-3">
                    <div className="flex items-center justify-between"><span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Supporting Evidence</span><button onClick={() => handleManualReverify(selectedClaim.id)} disabled={isVerifying[selectedClaim.id]} className="text-[10px] text-brand-400 font-semibold underline disabled:opacity-50">{isVerifying[selectedClaim.id] ? 'Verifying…' : 'Re-verify'}</button></div>
                    {selectedClaim.sources?.length ? selectedClaim.sources.map((source) => (
                      <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="block p-3 bg-slate-900/30 hover:bg-slate-900/60 border border-slate-800 rounded-xl transition"><div className="flex items-center justify-between gap-2"><p className="text-[10px] font-semibold text-slate-300 truncate">{source.title}</p><ExternalLink className="h-3.5 w-3.5 text-brand-400 flex-none" /></div>{source.snippet && <p className="text-[10px] text-slate-500 mt-1.5 leading-relaxed">{source.snippet}</p>}</a>
                    )) : <p className="text-xs text-slate-500">No trusted evidence was retrieved for this claim.</p>}
                  </div>
                </div>
              ) : <div className="glass p-8 text-center rounded-2xl h-[480px] flex flex-col items-center justify-center space-y-2"><HelpCircle className="h-8 w-8 text-slate-600" /><p className="text-xs font-semibold text-slate-400">Select an extracted claim to inspect its evidence.</p></div>}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
