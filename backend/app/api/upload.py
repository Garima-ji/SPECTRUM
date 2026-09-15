import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import crud
from app.database.connection import SessionLocal, get_db
from app.config import settings
from app.services import audio_service, claim_service, verification_service, whisper_service

logger = logging.getLogger("spectrum")
router = APIRouter(prefix="/upload", tags=["Upload"])


def _store_verification(db: Session, claim_id: int, result: dict):
    crud.update_claim_verification(
        db=db,
        claim_id=claim_id,
        verdict=result["verdict"],
        status=result["status"],
        reasoning=result["reasoning"],
        confidence=result.get("confidence"),
        confidence_factors=result.get("confidence_factors"),
        verification_status="completed",
    )
    crud.replace_sources(db, claim_id, result["sources"])


def process_audio_pipeline(audio_id: int, file_path: str):
    """Run transcription, claim extraction, retrieval, and verification for one upload.

    Pipeline stages written to AudioRecord.status:
        processing          → Whisper is loading / transcribing
        transcribed         → Transcript saved; Ollama claim extraction starting
        completed           → All stages finished successfully
        failed              → Fatal error at any stage (transcript may still be present)

    IMPORTANT: transcript_text is committed to the DB immediately after Whisper
    completes (before Ollama starts), so it remains available even if later stages fail.
    """
    db = SessionLocal()
    pipeline_t0 = time.perf_counter()
    try:
        logger.info("[Pipeline] Started audio_id=%s.", audio_id)

        # ── Stage 1: Transcription ────────────────────────────────────────────
        logger.info("[Whisper] Starting transcription for audio_id=%s.", audio_id)
        transcription = whisper_service.transcribe_audio(file_path)
        transcript = transcription["text"]
        timing = transcription.get("timing", {})

        if timing.get("model_load_s") is not None:
            logger.info("[Whisper] Model loaded in %.2fs.", timing["model_load_s"])
        logger.info("[Whisper] Transcription completed in %.2fs.", timing.get("inference_s", 0.0))

        # Commit transcript immediately so the API can return it right away,
        # before Ollama claim extraction starts.
        crud.update_audio_record_status(
            db,
            audio_id,
            status="transcribed",
            transcript_text=transcript,
            duration=transcription["duration"],
        )
        logger.info(
            "[Pipeline] Transcript saved for audio_id=%s. audio_duration=%.2fs.",
            audio_id,
            transcription["duration"],
        )
        logger.info("[Pipeline] Status=transcribed for audio_id=%s.", audio_id)

        # ── Stage 2: Claim extraction ─────────────────────────────────────────
        try:
            t_claims = time.perf_counter()
            logger.info("[Claims] Starting extraction for audio_id=%s.", audio_id)
            extracted_claims = claim_service.extract_claims_from_segments(transcription.get("segments", []))
            if not extracted_claims:
                logger.info("[Claims] Segment grouping returned 0 claims; falling back to Ollama extraction.")
                extracted_claims = claim_service.extract_claims(transcript, transcription.get("segments"))
            claims_elapsed = time.perf_counter() - t_claims
            logger.info("[Pipeline] Claim extraction completed in %.2fs.", claims_elapsed)
            logger.info(
                "[Claims] Parsed %d claims for audio_id=%s.",
                len(extracted_claims),
                audio_id,
            )
            logger.info("[Claims] Saving %d claims for audio_id=%s.", len(extracted_claims), audio_id)

            claims_to_verify = []
            for item in extracted_claims:
                claim = crud.create_claim(
                    db,
                    audio_id=audio_id,
                    claim_text=item["claim_text"],
                    speaker=item.get("speaker"),
                    timestamp=item.get("timestamp"),
                )
                claims_to_verify.append(claim)

            # Mark status so the frontend knows claims are saved and verification is starting.
            crud.update_audio_record_status(db, audio_id, status="claims_extracted")

            # ── Stage 3: Verification ─────────────────────────────────────
            def verify_and_store(claim_text: str, cid: int):
                logger.info("[Verification] Starting verification for claim_id=%s.", cid)
                try:
                    res = verification_service.verify_single_claim(
                        claim_text,
                        generate_query=False,
                        live_mode=True,
                    )
                    return cid, res, None
                except Exception as e:
                    return cid, None, e

            t_verify = time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(verify_and_store, item.claim_text, item.id): item
                    for item in claims_to_verify
                }
                
                for future in as_completed(futures):
                    cid, res, err = future.result()
                    if err:
                        logger.exception("[Verification][ERROR] Failed for claim_id=%s.", cid)
                        crud.update_claim_verification(
                            db,
                            cid,
                            verdict="UNVERIFIABLE",
                            status="No Reliable Update",
                            reasoning=f"Verification failed: {err}",
                            confidence=0.0,
                            confidence_factors=None,
                            verification_status="failed",
                        )
                    elif res:
                        _store_verification(db, cid, res)
                        logger.info(
                            "[Verification] claim_id=%s verdict=%s confidence=%.3f.",
                            cid,
                            res["verdict"],
                            res.get("confidence", 0.0),
                        )
                        
            verify_elapsed = time.perf_counter() - t_verify
            logger.info("[Pipeline] Verification of all claims completed in %.2fs.", verify_elapsed)

            logger.info("[Verification] Completed all claims for audio_id=%s.", audio_id)
            crud.update_audio_record_status(db, audio_id, status="completed")
            
            total_time = time.perf_counter() - pipeline_t0
            logger.info("[Pipeline] Completed audio_id=%s in %.2fs total.", audio_id, total_time)

        except Exception as exc:
            # Ollama / claim extraction failed.
            # IMPORTANT: transcript_text is already committed above — it stays in the DB.
            # Set status="failed" (not "transcribed") so the frontend stops polling.
            # The Dashboard renders transcript_text independently of the status field,
            # so the transcript will still be visible in the UI.
            logger.exception(
                "[Claims][ERROR] Claim extraction failed for audio_id=%s: %s", audio_id, exc
            )
            crud.update_audio_record_status(
                db,
                audio_id,
                status="failed",
                error_message=f"Claim extraction failed: {exc}",
            )
            logger.info(
                "[Pipeline] Status=failed (transcript preserved) for audio_id=%s.", audio_id
            )

    except Exception as exc:
        # Whisper itself failed — no transcript available.
        logger.exception("[Pipeline][ERROR] Whisper failed for audio_id=%s.", audio_id)
        crud.update_audio_record_status(db, audio_id, status="failed", error_message=str(exc))
    finally:
        db.close()


@router.post("/")
async def upload_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not audio_service.is_allowed_file(file.filename):
        raise HTTPException(
            status_code=400,
            detail="Invalid audio file. Allowed extensions: mp3, wav, m4a, flac, ogg, aac.",
        )
    try:
        file_path = audio_service.save_uploaded_audio(file)
        record = crud.create_audio_record(db, filename=file.filename, file_path=file_path)
        logger.info("[Upload] Saved audio_id=%s filename=%s.", record.id, record.filename)
        background_tasks.add_task(process_audio_pipeline, record.id, file_path)
        return {
            "message": "Audio uploaded. Transcription and verification have started.",
            "audio_id": record.id,
            "filename": record.filename,
            "status": record.status,
        }
    except Exception as exc:
        logger.exception("[Upload][ERROR] Could not save uploaded audio.")
        raise HTTPException(status_code=500, detail="The audio file could not be saved.") from exc
