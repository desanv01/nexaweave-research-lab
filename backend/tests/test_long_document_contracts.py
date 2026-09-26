"""Characterize bounded ontology context sampling, not full document recall."""

from app.services.ontology_generator import OntologyGenerator


def sampler(*, limit=2200, chunk_size=500, selected=3):
    generator = OntologyGenerator.__new__(OntologyGenerator)
    generator.MAX_TEXT_LENGTH_FOR_LLM = limit
    generator.LONG_TEXT_CHUNK_SIZE = chunk_size
    generator.LONG_TEXT_CHUNK_OVERLAP = 0
    generator.MAX_LONG_TEXT_CHUNKS = selected
    generator.MIN_LONG_TEXT_EXCERPT = 100
    return generator


def test_middle_and_ends_of_long_unicode_document_enter_bounded_context():
    generator = sampler(limit=1800)
    beginning = "BEGIN-🪐-起"
    text = beginning + "甲" * (500 - len(beginning)) + "乙" * 500 + "MIDDLE-🌊-中" + "丙" * 480 + "丁" * 500 + "END-🦉-終"
    assert len(text) > generator.MAX_TEXT_LENGTH_FOR_LLM
    context = generator._build_document_context([text])
    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "BEGIN-🪐-起" in context
    assert "MIDDLE-🌊-中" in context
    assert "END-🦉-終" in context
    assert context.count("--- 文档 1 / 分块") == 3
    assert "长文本自动分块摘要" in context


def test_many_documents_sample_first_middle_and_last_with_document_labels():
    generator = sampler(limit=2800, chunk_size=1000, selected=5)
    documents = [f"DOC-{number:02d}-START " + (chr(65 + number % 26) * 650) + f" DOC-{number:02d}-END" for number in range(1, 21)]
    context = generator._build_document_context(documents)
    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert context.count("--- 文档 ") == 5
    assert "--- 文档 1 / 分块 1/1 ---" in context
    assert "--- 文档 11 / 分块 1/1 ---" in context
    assert "--- 文档 20 / 分块 1/1 ---" in context
    assert "DOC-01-START" in context and "DOC-20-END" in context
    assert "DOC-02-START" not in context


def test_unicode_short_documents_preserve_join_and_empty_document_position():
    generator = sampler()
    documents = ["首文 🧪 café", "", "آخر 文書 🌱"]
    assert generator._build_document_context(documents) == "\n\n---\n\n".join(documents)


def test_many_unicode_documents_stay_within_character_limit():
    generator = sampler(limit=2400, chunk_size=300, selected=8)
    documents = [f"資料-{i:02d}-🧭 " + "漢字🙂" * 120 for i in range(30)]
    context = generator._build_document_context(documents)
    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "資料-00-🧭" in context
    assert "文档 30" in context
    assert "資料-29-🧭" in context or "漢字🙂" in context
