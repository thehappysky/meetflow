from functools import lru_cache
from importlib.util import find_spec

from sqlalchemy import select

from .audio_storage import audio_file
from .database import Meeting, Run, Source, row_dict
from .domain import DomainError, SourceInput, Span


def audio_available(settings):
    if settings.asr_backend == 'sensevoice':
        from .speech import model_files
        return bool(find_spec('sherpa_onnx') and find_spec('faster_whisper') and
                    all(p.is_file() for p in model_files(settings.speech_model_dir, settings.speaker_diarization)))
    return bool(settings.asr_model and find_spec("faster_whisper"))


@lru_cache(maxsize=1)
def load_model(model_name, device):
    from faster_whisper import WhisperModel
    return WhisperModel(model_name, device=device, compute_type="int8" if device == "cpu" else "float16")


def transcribe_spans(path, settings, check=lambda: None):
    if settings.asr_backend == 'sensevoice':
        from .speech import transcribe
        return transcribe(path, settings, check)
    model = load_model(settings.asr_model, settings.asr_device)
    segments, _ = model.transcribe(path, vad_filter=True, word_timestamps=True)
    spans = []
    for segment in segments:
        check()
        if segment.text.strip():
            spans.append({'id': str(len(spans)), 'text': segment.text.strip(), 'speaker': '未知说话人',
                          'start': segment.start, 'end': segment.end})
    return spans


def ensure_transcript(store, run_id):
    run = store.get_run(run_id)
    with store.sessions() as session:
        meeting = session.get(Meeting, run["meeting_id"])
        audio_path = meeting.audio_path
    if not audio_path or any(source["kind"] == "transcript" and source["meeting_id"] == meeting.id
                             for source in run["manifest"]["sources"]):
        return
    if not audio_available(store.settings):
        raise DomainError("音频转写未配置；请安装 audio 依赖并设置 ASR 模型。不会替换为演示文字", 422)
    store.update_run(run_id, phase="录音转写")
    store.event(run_id, "transcription_started", "正在转写录音，说话人身份需人工确认", {})
    spans = [Span(**span) for span in transcribe_spans(audio_file(store.settings.data_dir, audio_path),
             store.settings, lambda: store.check_active(run_id))]
    if not spans:
        raise DomainError("未检测到可识别的语音，未生成会议内容", 422)
    with store.sessions.begin() as session:
        store.fence(session, run_id)
        existing = session.scalar(select(Source).where(Source.meeting_id == meeting.id, Source.kind == "transcript"))
        source = existing or store.add_source(session, run["project_id"],
            SourceInput(title=meeting.title, kind="transcript", spans=spans), meeting.id)
        row = session.get(Run, run_id)
        manifest = dict(row.manifest)
        manifest["sources"] = [*manifest["sources"], row_dict(source)]
        row.manifest = manifest
        store.audit(session, "audio.transcribed", meeting.id,
                    {"backend": store.settings.asr_backend, "model": store.settings.asr_model, "segments": len(spans)})
    store.event(run_id, "transcription_completed", "录音已转写，说话人仍需人工确认", {"segments": len(spans)})
