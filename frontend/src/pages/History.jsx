import React, { useEffect, useState } from 'react';
import { FileAudio, ChevronRight, RefreshCw, AlertTriangle, MessageSquare } from 'lucide-react';
import api from '../services/api';

export default function History({ onSelectAudio }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchHistory = async () => {
    setLoading(true);
    try {
      const data = await api.getDashboardHistory(0, 50);
      setHistory(data);
      setError(false);
    } catch (err) {
      console.error(err);
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHistory();
  }, []);

  const formatDate = (dateString) => {
    try {
      const options = { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
      return new Date(dateString).toLocaleDateString(undefined, options);
    } catch (_error) {
      return dateString;
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case 'completed':
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Completed</span>;
      case 'processing':
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-brand-500/10 text-brand-400 border border-brand-500/20 animate-pulse">Transcribing</span>;
      case 'transcribed':
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 animate-pulse">Extracting</span>;
      case 'claims_extracted':
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse">Verifying</span>;
      case 'failed':
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-red-500/10 text-red-400 border border-red-500/20">Failed</span>;
      default:
        return <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700">Unknown</span>;
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-96 space-y-3">
        <RefreshCw className="h-7 w-7 text-brand-400 animate-spin" />
        <p className="text-xs text-slate-400">Loading audit history...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-96 space-y-4 text-center">
        <AlertTriangle className="h-10 w-10 text-red-400" />
        <div className="space-y-1">
          <p className="text-sm font-semibold text-slate-200">Failed to load history</p>
          <p className="text-xs text-slate-400">Please check backend database connectivity.</p>
        </div>
        <button
          onClick={fetchHistory}
          className="text-xs bg-slate-900 border border-slate-800 text-slate-300 px-4 py-2 rounded-xl hover:bg-slate-850"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white">Verification History</h1>
          <p className="text-slate-400 text-xs mt-1">Audit log of all uploaded audios, claim extractions, and verification outcomes.</p>
        </div>
        <button
          onClick={fetchHistory}
          className="p-2 bg-slate-900 hover:bg-slate-850 text-slate-300 rounded-lg border border-slate-800 transition"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {history.length === 0 ? (
        <div className="glass p-12 text-center rounded-2xl space-y-4">
          <FileAudio className="h-12 w-12 text-slate-600 mx-auto" />
          <div className="space-y-1">
            <p className="text-sm font-semibold text-slate-300">No transcripts found</p>
            <p className="text-xs text-slate-500">Go to "Verify Audio" to upload your first audio file.</p>
          </div>
        </div>
      ) : (
        <div className="glass rounded-2xl overflow-hidden shadow-xl border border-slate-800/80">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-800/80 bg-slate-900/40 text-xs font-semibold text-slate-400">
                  <th className="p-4.5">Filename</th>
                  <th className="p-4.5">Status</th>
                  <th className="p-4.5">Claims</th>
                  <th className="p-4.5">Processed Date</th>
                  <th className="p-4.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-xs text-slate-300">
                {history.map((record) => (
                  <tr 
                    key={record.id} 
                    className="hover:bg-slate-900/30 transition-colors duration-150 cursor-pointer"
                    onClick={() => onSelectAudio(record.id)}
                  >
                    <td className="p-4.5 font-semibold text-slate-200">
                      <div className="flex items-center space-x-2.5">
                        <FileAudio className="h-4.5 w-4.5 text-brand-400/80" />
                        <span className="truncate max-w-xs">{record.filename}</span>
                      </div>
                    </td>
                    <td className="p-4.5">{getStatusBadge(record.status)}</td>
                    <td className="p-4.5">
                      <div className="flex items-center space-x-1.5 text-slate-400">
                        <MessageSquare className="h-3.5 w-3.5" />
                        <span>{record.claims_count || 0} claims</span>
                      </div>
                    </td>
                    <td className="p-4.5 text-slate-400">{formatDate(record.created_at)}</td>
                    <td className="p-4.5 text-right">
                      <button className="text-brand-400 hover:text-brand-300 font-semibold inline-flex items-center space-x-1">
                        <span>View Details</span>
                        <ChevronRight className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
