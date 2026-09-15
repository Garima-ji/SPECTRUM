# Spectrum Research Notes & Optimizations

This document explains the choices made during the integration of Faster-Whisper and Qwen LLM.

## Speech-to-Text: Faster-Whisper
Faster-Whisper is a reimplementation of OpenAI's Whisper model using CTranslate2, which is a fast inference engine for Transformer models.
- **Why Faster-Whisper?** It runs up to 4x faster than the original whisper library and uses less memory while maintaining the exact same transcription accuracy.
- **CPU Optimizations**: In Docker CPU environments, we initialize with `compute_type="int8"`. This quantizes the model weights from float32/float16 to 8-bit integers, drastically saving memory and boosting performance with negligible loss of accuracy.
- **Model Choice**: By default, we use `base` or `tiny` for local/CPU execution. In production/GPU environments, `medium` or `large-v3` can be loaded.

## LLM: Qwen 2.5 (via Ollama)
Qwen 2.5 is Alibaba's state-of-the-art open-source LLM, known for its high reasoning capabilities, code writing skills, and strict JSON follow instructions.
- **Why Ollama?** Ollama makes running LLMs locally extremely straightforward. It handles CPU/GPU offloading automatically.
- **JSON Format Constraint**: Ollama's API has a `"format": "json"` constraint. By supplying this, the engine forces the model to structure output in syntactically valid JSON. This prevents LLM output issues where extra conversational text is added around JSON blocks.
- **Prompt Isolation**: System prompts are stored as separate text files in the `backend/app/prompts/` folder. This separates prompting code from routing logic, facilitating rapid prompt engineering updates without recompiling backend code.
