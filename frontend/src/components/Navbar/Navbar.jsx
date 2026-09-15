import React from 'react';
import { Activity, Sparkles } from 'lucide-react';

export default function Navbar() {
  return (
    <nav className="glass sticky top-0 z-50 px-6 py-4 flex items-center justify-between border-b border-slate-800">
      <div className="flex items-center space-x-3">
        <div className="bg-brand-500/10 p-2.5 rounded-xl border border-brand-500/25 shadow-lg shadow-brand-500/5">
          <Activity className="h-6 w-6 text-brand-400 animate-pulse" />
        </div>
        <div>
          <span className="text-xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-100 to-brand-400 bg-clip-text text-transparent">
            Spectrum
          </span>
          <span className="ml-2 text-xs font-semibold px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-300 border border-brand-500/20">
            AI Engine Active
          </span>
        </div>
      </div>
      
      <div className="flex items-center space-x-6">
        <div className="hidden md:flex items-center space-x-2 text-sm text-slate-400">
          <Sparkles className="h-4 w-4 text-amber-400" />
          <span>Whisper + Qwen 2.5 Fact-Checking</span>
        </div>
        <div className="flex items-center space-x-2 bg-slate-900 px-3.5 py-1.5 rounded-lg border border-slate-800">
          <div className="h-2 w-2 rounded-full bg-emerald-500 animate-ping"></div>
          <span className="text-xs font-medium text-emerald-400">System Ready</span>
        </div>
      </div>
    </nav>
  );
}
