# SPECTRUM

**Speech-based Promise & Evidence Classification with Temporal Retrieval in Unstructured Media**

SPECTRUM is an AI-powered claim analysis system that processes speeches and news broadcasts to detect check-worthy claims, retrieve evidence from trusted sources, analyze the implementation status of government initiatives or public commitments, and perform evidence-based fact verification.

Unlike traditional fact-checking systems that only determine whether a claim is true or false, SPECTRUM also tracks the current implementation status of claims related to policies, schemes, infrastructure projects, and public promises.

---

## Features

- Speech-to-text transcription using Faster-Whisper
- Automatic extraction of check-worthy claims
- Claim normalization for improved retrieval
- Intelligent search query generation
- Evidence retrieval from trusted sources
- Trusted source ranking and filtering
- Implementation status analysis
- Evidence-based fact verification
- Interactive dashboard with explanations and source links
- Support for English, Hindi, and code-switched speech

---

## Project Architecture

```
                Audio / Speech
                      │
                      ▼
          Speech Recognition (Whisper)
                      │
                      ▼
                 Transcript
                      │
                      ▼
            Claim Extraction Engine
                      │
                      ▼
             Claim Normalization
                      │
                      ▼
          Search Query Generation
                      │
                      ▼
            Evidence Retrieval
                      │
                      ▼
        Trusted Source Filtering
                      │
                      ▼
           Claim Analysis Engine
          ┌────────────┴────────────┐
          ▼                         ▼
Implementation Status      Fact Verification
          │                         │
          └────────────┬────────────┘
                       ▼
                React Dashboard
```

---

## Tech Stack

### Frontend

- React
- Vite
- Tailwind CSS
- Axios

### Backend

- FastAPI
- Python

### AI Models

- Faster-Whisper
- Qwen 2.5 (via Ollama)

### Search & Retrieval

- DuckDuckGo Search
- Trusted Source Ranking

### Database

- PostgreSQL

### Deployment

- Docker
- Docker Compose

---

## Repository Structure

```
SPECTRUM/
│
├── frontend/
├── backend/
├── models/
├── data/
├── docs/
├── docker/
├── docker-compose.yml
└── README.md
```

---

## Quick Start & Running the Project

The easiest way to run the entire system is using Docker Compose. All services are containerized, health-checked, and sequenced.

### Step 1: Start the services
From the project root directory, run:
```bash
docker-compose up --build
```
This command starts:
- `spectrum_db`: PostgreSQL database on port `5432` (with automated healthchecks).
- `spectrum_ollama`: Ollama server on port `11434` (with automated healthchecks).
- `spectrum_ollama_init`: A service that automatically runs at startup to check if the configured LLM model exists and downloads it if missing.
- `spectrum_backend`: FastAPI backend on port `8000` (starts only after the database is healthy and the model is fully pulled).
- `spectrum_frontend`: React UI dashboard on port `5173`.

### Step 2: Open the app
Once all startup logs have stabilized and the model is downloaded, visit:
**`http://localhost:5173`**

---

## Configuration & Environment Variables

You can customize the models and execution settings inside `docker-compose.yml` or by creating a `.env` file in the `backend/` directory:

| Environment Variable | Default Value | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/spectrum` | PostgreSQL database connection URL |
| `OLLAMA_URL` | `http://ollama:11434` | The host address for the Ollama API |
| `OLLAMA_MODEL` | `qwen2.5:latest` | The model name to pull and use for analysis |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Timeout threshold for LLM generation requests |
| `WHISPER_MODEL` | `small` | Faster-Whisper model (e.g. `tiny`, `base`, `small`, `medium`) |
| `WHISPER_DEVICE` | `cpu` | Device hardware acceleration: `cpu` or `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | Compute quantization precision (int8 is optimal for CPU) |
| `WHISPER_LANGUAGE` | `""` | Forced language code. Empty string = auto-detect language. Set to `hi` to force Hindi Devanagari output |
| `WHISPER_TASK` | `transcribe` | Transcription task mode |

*Note: The project mounts a `whisper_cache` volume to cache downloaded speech-to-text models on the host, preventing redownloads when containers restart.*

---

## Multilingual Support (Hindi & English)

SPECTRUM has been upgraded to support English, Hindi, and code-switched (mixed) speech:
- **Language Detection**: Automatically detects the speech language from the first few seconds of audio.
- **Forced Devanagari (Hindi)**: When Hindi is specified (`WHISPER_LANGUAGE=hi`), the system forces Devanagari script output to prevent auto-detect failures that can lead to related scripts like Urdu.
- **Urdu-Script Filter**: If a transcription pass for Hindi results in Urdu script, the system automatically triggers a retry with a higher beam size (`beam_size=5`) to correct spelling or rejects it if it cannot be parsed in Devanagari.

---

## Running Tests

SPECTRUM contains testing suites to verify API and AI pipeline integrity:

### 1. Test Ollama & Prompts
To test if Ollama is connected and the claim-extraction prompts parse correctly inside the container, run:
```bash
docker exec -it spectrum_backend python test_ollama.py
```

### 2. Run Pytest Suite
To run the automated tests checking language routing, Urdu-script guards, and fact-checking verifications, run:
```bash
docker exec -it spectrum_backend pytest test_pipeline.py
```

---

## Workflow

1. Receive live or recorded speech.
2. Convert speech to text using Faster-Whisper.
3. Extract check-worthy claims.
4. Normalize claims.
5. Generate optimized search queries.
6. Retrieve evidence from trusted sources.
7. Rank and filter evidence.
8. Analyze implementation status.
9. Perform evidence-based fact verification.
10. Display results on the dashboard.

---

## Output

For every detected claim, SPECTRUM provides:

- Claim
- Implementation Status
    - Completed
    - In Progress
    - Delayed
    - Not Started
    - No Reliable Update
- Fact Verification
    - True
    - Mostly True
    - Misleading
    - False
    - Unverifiable
- Explanation
- Supporting Sources

---

## Objectives

- Detect check-worthy claims from spoken content.
- Analyze the progress of public initiatives and commitments.
- Verify factual accuracy using trusted evidence.
- Provide transparent reasoning supported by reliable sources.
- Enable multilingual claim analysis for English, Hindi, and mixed-language speech.

---

## Future Scope

- Live streaming support
- Speaker diarization
- Advanced Retrieval-Augmented Generation (RAG)
- Vector database integration
- Real-time notification system
- Multi-language expansion
- Performance evaluation dashboard

---

## License

This project is being developed as an academic Final Year Project.
