# Spectrum API Endpoints

All API endpoints are prefixed with `/api` by default.

## 1. Upload Router (`/api/upload`)
- **`POST /api/upload/`**
  - **Payload**: Multipart file upload (`file: UploadFile`).
  - **Description**: Receives audio files, saves them, creates db structures, and schedules background processing.
  - **Response**:
    ```json
    {
      "message": "Audio file uploaded and processing started in background.",
      "audio_id": 1,
      "filename": "speech.mp3",
      "status": "processing"
    }
    ```

## 2. Transcript Router (`/api/transcript`)
- **`GET /api/transcript/{audio_id}`**
  - **Description**: Returns transcripts and metadata of a specific audio file.
  - **Response**:
    ```json
    {
      "audio_id": 1,
      "filename": "speech.mp3",
      "status": "completed",
      "transcript_text": "The government built 50 schools...",
      "duration": 0.0,
      "created_at": "2026-08-02T13:30:00Z"
    }
    ```

## 3. Claims Router (`/api/claims`)
- **`GET /api/claims/{audio_id}`**
  - **Description**: Returns all check-worthy claims extracted from the transcript.
  - **Response**:
    ```json
    {
      "audio_id": 1,
      "claims": [
        {
          "id": 1,
          "claim_text": "The government completed 50 new schools.",
          "speaker": "Speaker 1",
          "timestamp": "00:02",
          "verdict": "FALSE",
          "confidence": 0.92,
          "verification_status": "completed",
          "status": "In Progress",
          "reasoning": "Evidence shows only 10 schools are open...",
          "sources": [
            {
              "title": "News article",
              "url": "https://example.com",
              "snippet": "Only 10 schools completed..."
            }
          ]
        }
      ]
    }
    ```

## 4. Verify Router (`/api/verify`)
- **`POST /api/verify/claim/{claim_id}`**
  - **Description**: Re-runs or manually triggers verification for a specific claim.
- **`GET /api/verify/claim/{claim_id}`**
  - **Description**: Gets status and results for a single claim.

## 5. Dashboard Router (`/api/dashboard`)
- **`GET /api/dashboard/stats`**
  - **Description**: Fetches platform metrics (total audios, total claims, truth rate).
- **`GET /api/dashboard/history`**
  - **Description**: Fetches history log of all processing transactions.
