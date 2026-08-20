"""Unit tests for spoken sentence chunking (spec 005)."""

from local_tts.text_chunk import split_spoken_sentences


def test_empty_and_whitespace():
    assert split_spoken_sentences("") == []
    assert split_spoken_sentences("   ") == []
    assert split_spoken_sentences(None) == []


def test_single_sentence():
    assert split_spoken_sentences("Hello there.") == ["Hello there."]
    assert split_spoken_sentences("No punctuation here") == ["No punctuation here"]


def test_multi_sentence():
    text = "Hello world. How are you? I am fine!"
    assert split_spoken_sentences(text) == [
        "Hello world.",
        "How are you?",
        "I am fine!",
    ]


def test_collapses_whitespace():
    assert split_spoken_sentences("Hi.\n\n  Next.") == ["Hi.", "Next."]


def test_trailing_quote_stays_with_sentence():
    out = split_spoken_sentences('He said "hi." Then left.')
    assert out[0].endswith('"') or out[0].endswith('."')
    assert len(out) >= 2


def test_markdownish_single_blob_ok():
    # No hard requirement to strip markdown; just return usable chunks.
    out = split_spoken_sentences("Use **bold** text. Then more.")
    assert len(out) == 2
    assert "bold" in out[0]
