import pytest

from scripts.run_audio_benchmark import edits, normalized, safe_target


def test_audio_metric_normalization_and_known_edit_cases():
    assert normalized("客户：ＡＢＣ，预算１２元！") == "客户abc预算12元"
    assert edits("需求待定", "需求确定") == 1
    assert edits("先发资料", "发资料") == 1
    assert edits("", "新增") == 2


def test_installed_optional_asr_decodes_real_wav(tmp_path):
    import wave
    audio = pytest.importorskip("faster_whisper.audio")
    path = tmp_path / "one-second.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    # Exercises the actual faster-whisper -> PyAV API, without model downloads or a decoder mock.
    decoded = audio.decode_audio(str(path))
    assert len(decoded) == 16000


@pytest.mark.parametrize("name", ["../escape.wav", "safe/../../escape.wav", "/outside.wav"])
def test_audio_archive_cannot_escape_download_directory(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        safe_target(tmp_path, name)
    assert safe_target(tmp_path, "Eval/audio/clip.wav").is_relative_to(tmp_path)
