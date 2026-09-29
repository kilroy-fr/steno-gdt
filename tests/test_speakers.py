"""Sprecherzuordnung: Wort -> Sprecher, satzweise Korrektur, Glaettung, Marker."""
import server
from server import format_diarized_transcript, has_speaker_labels


def u(text, start, end):
    return {"start": start, "end": end, "text": text}


def seg(speaker, start, end):
    return {"start": start, "end": end, "speaker": speaker}


def test_has_speaker_labels():
    assert has_speaker_labels("SPRECHER A: Hallo\nSPRECHER B: Tag")
    assert has_speaker_labels("Vorspann\nSPRECHER 11: x")
    assert not has_speaker_labels("Der SPRECHER A: sagte")
    assert not has_speaker_labels("Hallo")


def test_speaker_at_largest_overlap_then_nearest():
    segments = [seg("X", 0, 1.0), seg("Y", 0.9, 3.0)]
    assert server._speaker_at(0.8, 1.2, segments) == "Y"   # 0.3 s gegen 0.2 s
    assert server._speaker_at(0.1, 0.5, segments) == "X"
    # Kein Ueberlapp: naechstgelegene Grenze.
    assert server._speaker_at(3.5, 3.6, segments) == "Y"


def test_sentence_goes_to_majority_speaker():
    units = [u(" Ja,", 0, 1), u(" ich", 1, 2), u(" wollte", 2, 3), u(" einen", 3, 4),
             u(" ausmachen.", 4, 5), u(" Gut.", 5, 6)]
    speakers = ["B", "B", "B", "B", "A", "A"]
    assert server._realign_sentences(units, speakers) == ["B"] * 5 + ["A"]


def test_sentence_tie_is_left_alone():
    units = [u(" eins", 0, 1), u(" zwei.", 1, 2)]
    assert server._realign_sentences(units, ["A", "B"]) == ["A", "B"]


def test_long_sentence_is_left_alone():
    units = [u(" w", i, i + 1) for i in range(server._REALIGN_MAX_WORDS + 1)]
    speakers = ["A"] * 30 + ["B"] * (len(units) - 30)
    assert server._realign_sentences(units, speakers) == speakers


def test_short_blip_is_merged_into_surrounding_speaker():
    units = [u(" Das", 0, 1), u(" ist", 1, 2), u(" mhm", 2.0, 2.3), u(" gut", 2.4, 3),
             u(" so.", 3, 4)]
    segments = [seg("A", 0, 2.0), seg("B", 2.0, 2.3), seg("A", 2.3, 4)]
    assert format_diarized_transcript(units, segments) == "SPRECHER A: Das ist mhm gut so."


def test_nested_blips_need_more_than_one_pass():
    turns = [
        {"speaker": "A", "units": [u(" a", 0, 1)] * 3, "start": 0, "end": 3},
        {"speaker": "B", "units": [u(" b", 3, 3.2)], "start": 3, "end": 3.2},
        {"speaker": "A", "units": [u(" a", 3.2, 3.4)], "start": 3.2, "end": 3.4},
        {"speaker": "C", "units": [u(" c", 3.4, 3.6)], "start": 3.4, "end": 3.6},
        {"speaker": "A", "units": [u(" a", 4, 5)] * 3, "start": 4, "end": 7},
    ]
    smoothed = server._smooth_turns(turns)
    assert [t["speaker"] for t in smoothed] == ["A"]


def test_format_two_speakers(fake):
    from conftest import LABELED, SEGMENTS, WORDS
    units = [u(w, s, e) for w, s, e in WORDS]
    assert format_diarized_transcript(units, SEGMENTS) == LABELED


def test_format_empty_input():
    assert format_diarized_transcript([], [seg("A", 0, 1)]) == ""
    assert format_diarized_transcript([u(" x", 0, 1)], []) == ""


def test_labels_after_ten_speakers_are_numbers():
    units, segments = [], []
    for i in range(12):
        units.append(u(f" Satz {i}.", i * 10, i * 10 + 5))
        units.append(u(" weiter.", i * 10 + 5, i * 10 + 8))
        segments.append(seg(f"S{i:02d}", i * 10, i * 10 + 9))
    lines = format_diarized_transcript(units, segments).splitlines()
    assert lines[0].startswith("SPRECHER A: ")
    assert lines[9].startswith("SPRECHER J: ")
    assert lines[10].startswith("SPRECHER 11: ")
