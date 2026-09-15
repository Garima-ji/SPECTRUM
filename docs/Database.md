# Spectrum Database Schema

This document details the PostgreSQL relational schemas utilized inside the **Spectrum** database.

## Table: `audio_records`
Stores the metadata and consolidated results of audio transcription.

| Field | Type | Attributes | Description |
|---|---|---|---|
| `id` | Integer | Primary Key, Auto-increment | Unique identifier |
| `filename` | String | Not Null | Original upload filename |
| `file_path` | String | Not Null | Path to the file on local disk |
| `duration` | Float | Nullable | Length of the audio in seconds |
| `transcript_text`| Text | Nullable | Combined transcript returned by Whisper |
| `status` | String | Default: `"processing"` | Pipeline status: `"processing"`, `"completed"`, `"failed"` |
| `created_at` | DateTime| Default: `utc_now` | Timestamp of upload |
| `updated_at` | DateTime| Default: `utc_now`, OnUpdate | Timestamp of last modification |

## Table: `claims`
Stores individual factual assertions extracted from the transcripts.

| Field | Type | Attributes | Description |
|---|---|---|---|
| `id` | Integer | Primary Key | Unique identifier |
| `audio_id` | Integer | ForeignKey (`audio_records.id`, OnDelete: Cascade) | Link to audio file |
| `claim_text` | Text | Not Null | Concise restatement of the claim |
| `speaker` | String | Nullable | Speaker identity if extracted |
| `timestamp` | String | Nullable | Timestamp where assertion occurred |
| `verdict` | String | Default: `"Unverifiable"` | Veracity: `"True"`, `"Mostly True"`, `"Misleading"`, `"False"`, `"Unverifiable"` |
| `status` | String | Default: `"Not Started"` | Implementation progress: `"Completed"`, `"In Progress"`, `"Delayed"`, `"Not Started"`, `"No Reliable Update"` |
| `reasoning` | Text | Nullable | LLM analysis justification |
| `created_at` | DateTime| Default: `utc_now` | Record creation date |

## Table: `sources`
Trusted external reference links fetched via DuckDuckGo Search.

| Field | Type | Attributes | Description |
|---|---|---|---|
| `id` | Integer | Primary Key | Unique identifier |
| `claim_id` | Integer | ForeignKey (`claims.id`, OnDelete: Cascade) | Link to verified claim |
| `title` | String | Nullable | Document/Article Title |
| `url` | String | Not Null | Reference URL |
| `snippet` | Text | Nullable | Extracted text snippet matching claim search |
| `created_at` | DateTime| Default: `utc_now` | Insertion timestamp |
