from meetflow.speech import speaker_for


def test_speaker_labels_are_anonymous_and_ambiguous_audio_stays_uncertain():
    turns = [{'start': 0, 'end': 5, 'speaker': 0}, {'start': 5, 'end': 10, 'speaker': 1}]
    assert speaker_for(0, 4, turns) == '说话人1（待确认）'
    assert speaker_for(4, 6, turns) == '说话人待确认'
    assert speaker_for(0, 1, []) == '未知说话人'
    turns.append({'start': 1, 'end': 2, 'speaker': 1})
    assert speaker_for(0, 3, turns) == '多人重叠／待确认'


def test_speaker_diagnostic_maps_labels_and_counts_miss_false_and_confusion():
    import pytest
    pytest.importorskip('numpy')
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from compare_speech import speaker_error
    clip = {'seconds': 7, 'offset': 0, 'intervals': [(0, 2, 'a', 'A'), (2, 4, 'b', 'B')]}
    swapped = [{'start': 0, 'end': 2, 'speaker': 9}, {'start': 2, 'end': 4, 'speaker': 3}]
    assert speaker_error(clip, swapped)['diagnostic_der'] == 0
    missing = speaker_error(clip, swapped[:1])
    assert missing['miss_frames'] > 0 and missing['confusion_frames'] == 0
    false = speaker_error(clip, [*swapped, {'start': 5, 'end': 6, 'speaker': 9}])
    assert false['false_frames'] > 0
    merged = speaker_error(clip, [{'start': 0, 'end': 4, 'speaker': 9}])
    assert merged['confusion_frames'] > 0 and merged['miss_frames'] == 0
