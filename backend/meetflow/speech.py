"""Optional CPU SenseVoice/VAD and anonymous speaker clustering, no identity inference."""
import re
from functools import lru_cache
from pathlib import Path

from .domain import DomainError

SENSE = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
SEGMENT = 'sherpa-onnx-pyannote-segmentation-3-0'


def model_files(directory, diarization=False):
    root = Path(directory)
    files = [root / SENSE / 'model.int8.onnx', root / SENSE / 'tokens.txt', root / 'silero_vad.onnx']
    if diarization:
        files.extend([root / SEGMENT / 'model.onnx', root / 'speaker.onnx'])
    return files


@lru_cache(maxsize=1)
def recognizer(directory):
    import sherpa_onnx as sherpa
    model, tokens, _ = model_files(directory)
    return sherpa.OfflineRecognizer.from_sense_voice(model=str(model), tokens=str(tokens),
        num_threads=2, use_itn=True, provider='cpu')


def diarize(samples, directory, check=lambda: None):
    import sherpa_onnx as sherpa
    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(Path(directory) / SEGMENT / 'model.onnx')), num_threads=2),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=str(Path(directory) / 'speaker.onnx'), num_threads=2),
        clustering=sherpa.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise DomainError('说话人模型配置无效，不会推断销售身份', 422)
    check()
    result = sherpa.OfflineSpeakerDiarization(config).process(samples,
        callback=lambda processed, total: (check() or 0)).sort_by_start_time()
    check()
    return [{'start': float(r.start), 'end': float(r.end), 'speaker': int(r.speaker)} for r in result]


def speaker_for(start, end, turns):
    """Only dominant non-overlapping anonymous labels; never guess a person's identity."""
    if not turns or end <= start:
        return '未知说话人'
    coverage = {}
    intersections = []
    for turn in turns:
        left, right = max(start, turn['start']), min(end, turn['end'])
        if right > left:
            coverage[turn['speaker']] = coverage.get(turn['speaker'], 0) + right - left
            intersections.append((left, right, turn['speaker']))
    if any(a < d and c < b and first != second
           for i, (a, b, first) in enumerate(intersections) for c, d, second in intersections[i + 1:]):
        return '多人重叠／待确认'
    if coverage:
        speaker = max(coverage, key=coverage.get)
        if coverage[speaker] / (end - start) >= 0.8:
            return f'说话人{speaker + 1}（待确认）'
    return '说话人待确认'


def transcribe(path, settings, check=lambda: None, with_turns=False):
    import sherpa_onnx as sherpa
    from faster_whisper.audio import decode_audio
    if not all(p.is_file() for p in model_files(settings.speech_model_dir, settings.speaker_diarization)):
        raise DomainError('语音模型文件缺失，请先准备模型；不会替换为演示转写', 422)
    samples = decode_audio(str(path), sampling_rate=16000)
    check()
    turns = diarize(samples, settings.speech_model_dir, check) if settings.speaker_diarization else []
    config = sherpa.VadModelConfig()
    config.silero_vad.model = str(Path(settings.speech_model_dir) / 'silero_vad.onnx')
    config.silero_vad.max_speech_duration = 15
    config.silero_vad.min_silence_duration = 0.35
    config.sample_rate = 16000
    detector = sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=60)
    model = recognizer(str(settings.speech_model_dir))
    spans = []

    def consume():
        while not detector.empty():
            check()
            speech = detector.front
            stream = model.create_stream()
            stream.accept_waveform(16000, speech.samples)
            model.decode_stream(stream)
            text = re.sub(r'<\|[^|]+\|>', '', stream.result.text).strip()
            start = speech.start / 16000
            end = min(len(samples) / 16000, start + len(speech.samples) / 16000)
            if text and end > start:
                spans.append({'id': str(len(spans)), 'text': text, 'start': start, 'end': end,
                              'speaker': speaker_for(start, end, turns)})
            detector.pop()
    for offset in range(0, len(samples), 512):
        check()
        detector.accept_waveform(samples[offset:offset + 512])
        consume()
    detector.flush()
    consume()
    return (spans, turns) if with_turns else spans
