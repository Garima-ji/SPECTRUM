import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const api = {
  // Upload Audio
  uploadAudio: async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiClient.post('/upload/', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  // Get Audio Transcript
  getTranscript: async (audioId) => {
    const response = await apiClient.get(`/transcript/${audioId}`);
    return response.data;
  },

  // Get Claims for an Audio File
  getClaims: async (audioId) => {
    const response = await apiClient.get(`/claims/${audioId}`);
    return response.data;
  },

  // Trigger manual claim verification
  verifyClaim: async (claimId) => {
    const response = await apiClient.post(`/verify/claim/${claimId}`);
    return response.data;
  },

  // Get Status of a specific claim
  getClaimStatus: async (claimId) => {
    const response = await apiClient.get(`/verify/claim/${claimId}`);
    return response.data;
  },

  // Get Dashboard Summary Stats
  getDashboardStats: async () => {
    const response = await apiClient.get('/dashboard/stats');
    return response.data;
  },

  // Get Dashboard Upload History
  getDashboardHistory: async (skip = 0, limit = 10) => {
    const response = await apiClient.get('/dashboard/history', {
      params: { skip, limit },
    });
    return response.data;
  },
};

export default api;
