"""Chunking + embeddings."""

import math

from packages.knowledge.chunking import chunk_document
from packages.knowledge.embeddings import HashingEmbedder, normalize, terms

DOC = """# Guide

Intro paragraph about controllers.

## E-ACT-401: Missing activation token

The controller refuses commissioning without a token.

Resolution steps:

1. Request a token.
2. Re-run commissioning.

## E-FW-118: Firmware checksum mismatch

""" + ("Firmware sentence number filler. " * 200)


def test_chunks_follow_headings_and_carry_section_headers():
    chunks = chunk_document(DOC, filename="guide.md", max_tokens=200)
    sections = [c.section for c in chunks]
    assert "Guide > E-ACT-401: Missing activation token" in sections
    act = next(c for c in chunks if c.section and "E-ACT-401" in c.section)
    assert act.content.startswith("[Guide > E-ACT-401: Missing activation token]")
    assert "1. Request a token." in act.content  # steps stay with their section
    assert all(c.token_count <= 200 + 5 for c in chunks)
    assert len([c for c in chunks if c.section and "E-FW-118" in c.section]) > 1  # long section split
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_normalization_is_consistent():
    assert normalize("installation") == normalize("installations") == "install"
    assert normalize("failed") == normalize("failing") == "fail"
    assert normalize("e-act-401") == "e-act-401"
    assert "the" not in terms("the installation failed")


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_embeddings_are_deterministic_normalized_and_meaningful():
    emb = HashingEmbedder()
    a1, a2 = emb.embed(["activation token missing"] * 2)
    assert a1 == a2 and len(a1) == 384
    assert math.isclose(sum(v * v for v in a1), 1.0, rel_tol=1e-6)
    related = emb.embed_one("controller missing its activation token during installation")
    unrelated = emb.embed_one("quarterly invoice payment schedule")
    assert cosine(a1, related) > cosine(a1, unrelated)
