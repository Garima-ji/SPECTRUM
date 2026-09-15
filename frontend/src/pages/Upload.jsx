import React, { useState } from 'react';
import { UploadCloud, FileAudio, CheckCircle2, AlertTriangle, RefreshCw } from 'lucide-react';
import api from '../services/api';

export default function Upload({ onUploadSuccess }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, uploading, processing, success, error
  const [errorMessage, setErrorMessage] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (selectedFile) => {
    const allowedExtensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac'];
    const extension = selectedFile.name.substring(selectedFile.name.lastIndexOf('.')).toLowerCase();
    
    if (allowedExtensions.includes(extension)) {
      setFile(selectedFile);
      setStatus('idle');
      setErrorMessage('');
    } else {
      setErrorMessage('Unsupported file format. Please upload MP3, WAV, M4A, FLAC, OGG, or AAC.');
      setStatus('error');
      setFile(null);
    }
  };

  const handleUploadSubmit = async () => {
    if (!file) return;
    setStatus('uploading');
    try {
      const res = await api.uploadAudio(file);
      setStatus('success');
      
      // Auto-navigate to dashboard for this audio after 2 seconds
      setTimeout(() => {
        onUploadSuccess(res.audio_id);
      }, 1500);
    } catch (err) {
      console.error(err);
      setStatus('error');
      setErrorMessage(err.response?.data?.detail || 'Failed to upload audio. Please try again.');
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-8 pt-8">
      <div className="text-center space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-white">Factcheck New Audio</h1>
        <p className="text-slate-400 max-w-md mx-auto text-sm leading-relaxed">
          Upload recorded speech or statements to transcribe audio, detect check-worthy claims, and view verified sources.
        </p>
      </div>

      <div className="glass p-8 rounded-3xl space-y-6 shadow-2xl relative overflow-hidden">
        {/* Decorative ambient background glow */}
        <div className="absolute -top-24 -left-24 w-48 h-48 bg-brand-500/10 rounded-full blur-3xl pointer-events-none"></div>

        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-2xl p-10 flex flex-col items-center justify-center space-y-4 cursor-pointer transition-all duration-300 ${
            isDragOver 
              ? 'border-brand-400 bg-brand-500/5' 
              : 'border-slate-800 hover:border-slate-700 bg-slate-900/10'
          }`}
          onClick={() => document.getElementById('file-upload-input').click()}
        >
          <input
            id="file-upload-input"
            type="file"
            className="hidden"
            accept=".mp3,.wav,.m4a,.flac,.ogg,.aac"
            onChange={handleFileChange}
          />
          
          <div className="p-4 bg-brand-500/10 rounded-full border border-brand-500/15">
            <UploadCloud className="h-8 w-8 text-brand-400" />
          </div>
          
          <div className="text-center space-y-1.5">
            <p className="text-sm font-semibold text-slate-200">
              {file ? file.name : "Drag & drop audio file here"}
            </p>
            <p className="text-xs text-slate-500">
              Supports MP3, WAV, M4A, FLAC up to 50MB
            </p>
          </div>
        </div>

        {file && status === 'idle' && (
          <div className="flex items-center justify-between p-4 bg-slate-900/60 rounded-xl border border-slate-800">
            <div className="flex items-center space-x-3">
              <FileAudio className="h-5 w-5 text-brand-400" />
              <div className="text-xs">
                <p className="font-semibold text-slate-200">{file.name}</p>
                <p className="text-slate-500">{(file.size / (1024 * 1024)).toFixed(2)} MB</p>
              </div>
            </div>
            <button
              onClick={handleUploadSubmit}
              className="bg-brand-500 hover:bg-brand-600 active:bg-brand-700 text-white text-xs font-semibold px-4.5 py-2.5 rounded-xl transition-all duration-200"
            >
              Analyze Audio
            </button>
          </div>
        )}

        {status === 'uploading' && (
          <div className="flex flex-col items-center justify-center p-6 space-y-4">
            <RefreshCw className="h-7 w-7 text-brand-400 animate-spin" />
            <div className="text-center space-y-1.5">
              <p className="text-sm font-semibold text-slate-200">Uploading audio file...</p>
              <p className="text-xs text-slate-500">Sending to server for transcription</p>
            </div>
          </div>
        )}

        {status === 'success' && (
          <div className="flex flex-col items-center justify-center p-6 space-y-4 text-center">
            <CheckCircle2 className="h-10 w-10 text-emerald-400" />
            <div className="space-y-1.5">
              <p className="text-sm font-semibold text-emerald-400">Upload Successful!</p>
              <p className="text-xs text-slate-400">Redirecting to Live Verification Dashboard...</p>
            </div>
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-center justify-center p-6 space-y-4 text-center border border-red-500/10 bg-red-500/5 rounded-2xl">
            <AlertTriangle className="h-10 w-10 text-red-500" />
            <div className="space-y-1.5">
              <p className="text-sm font-semibold text-red-400">Upload Error</p>
              <p className="text-xs text-slate-400 max-w-xs">{errorMessage}</p>
            </div>
            <button
              onClick={() => setStatus('idle')}
              className="text-xs text-slate-400 hover:text-slate-200 font-semibold underline"
            >
              Try Another File
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
