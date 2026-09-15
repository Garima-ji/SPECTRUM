import React from 'react';
import { LayoutDashboard, UploadCloud, History, Mic } from 'lucide-react';

export default function Sidebar({ activePage, setActivePage }) {
  const menuItems = [
    { id: 'dashboard', name: 'Dashboard', icon: LayoutDashboard },
    { id: 'upload', name: 'Verify Audio', icon: UploadCloud },
    { id: 'live', name: 'Live Mode', icon: Mic },
    { id: 'history', name: 'Audit Log', icon: History },
  ];

  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950/50 p-6 flex flex-col h-[calc(100vh-73px)]">
      <div className="space-y-6">
        <div className="text-xs font-bold uppercase tracking-wider text-slate-500 px-3">
          Navigation
        </div>
        <nav className="space-y-1.5">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActivePage(item.id)}
                className={`w-full flex items-center space-x-3.5 px-4 py-3 rounded-xl text-sm font-medium transition-all duration-200 ${isActive
                  ? 'bg-brand-500/10 text-brand-300 border border-brand-500/20 shadow-md shadow-brand-500/5'
                  : 'text-slate-400 hover:bg-slate-900/60 hover:text-slate-200 border border-transparent'
                  }`}
              >
                <Icon className={`h-5 w-5 ${isActive ? 'text-brand-400' : 'text-slate-400'}`} />
                <span>{item.name}</span>
              </button>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
