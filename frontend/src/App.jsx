import React, { useState } from 'react';
import Navbar from './components/Navbar/Navbar';
import Sidebar from './components/Sidebar/Sidebar';
import Dashboard from './pages/Dashboard';
import Upload from './pages/Upload';
import History from './pages/History';
import LiveMode from './pages/LiveMode';

export default function App() {
  const [activePage, setActivePage] = useState('upload');
  const [activeAudioId, setActiveAudioId] = useState(null);

  const handleUploadSuccess = (audioId) => {
    setActiveAudioId(audioId);
    setActivePage('dashboard');
  };

  const handleSelectAudio = (audioId) => {
    setActiveAudioId(audioId);
    setActivePage('dashboard');
  };

  const handleBackToHistory = () => {
    setActivePage('history');
  };

  const renderContent = () => {
    switch (activePage) {
      case 'dashboard':
        return (
          <Dashboard 
            audioId={activeAudioId} 
            onBackToHistory={handleBackToHistory} 
          />
        );
      case 'upload':
        return (
          <Upload 
            onUploadSuccess={handleUploadSuccess} 
          />
        );
      case 'history':
        return (
          <History 
            onSelectAudio={handleSelectAudio} 
          />
        );
      case 'live':
        return <LiveMode />;
      default:
        return <Upload onUploadSuccess={handleUploadSuccess} />;
    }
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* Navbar Header */}
      <Navbar />

      <div className="flex flex-1">
        {/* Sidebar Navigation */}
        <Sidebar activePage={activePage} setActivePage={setActivePage} />

        {/* Page Content Panel */}
        <main className="flex-1 p-8 bg-slate-950 overflow-y-auto h-[calc(100vh-73px)]">
          <div className="max-w-7xl mx-auto">
            {renderContent()}
          </div>
        </main>
      </div>
    </div>
  );
}
