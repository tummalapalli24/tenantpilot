"""Structure-aware chunking for knowledge documents.

Chunks follow the document's own structure (markdown headings, then paragraphs, then
sentences) instead of fixed character windows, and each chunk is prefixed with its
heading path ("Installation Guide > Activation errors"). That header makes a chunk
self-describing, which improves both retrieval and the model's ability to cite it.
"""

import hashlib
import re
from dataclasses import dataclass, field

from packages.core.tokens import estimate_tokens

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    index: int
    content: str
    section: str | None
    token_count: int
    content_hash: str
    metadata: dict = field(default_factory=dict)


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """Return (heading_path, body) pairs in document order."""
    sections: list[tuple[str | None, list[str]]] = []
    path: list[tuple[int, str]] = []
    current: list[str] = []
    current_heading: str | None = None
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            if current and "".join(current).strip():
                sections.append((current_heading, current))
            level, title = len(match.group(1)), match.group(2).strip()
            path = [(lvl, t) for lvl, t in path if lvl < level] + [(level, title)]
            current_heading = " > ".join(t for _, t in path)
            current = []
        else:
            current.append(line)
    if current and "".join(current).strip():
        sections.append((current_heading, current))
    return [(heading, "\n".join(lines).strip()) for heading, lines in sections]


def _pieces(body: str, max_tokens: int) -> list[str]:
    """Paragraphs, with oversized paragraphs split into sentences, then words."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if estimate_tokens(para) <= max_tokens:
            out.append(para)
            continue
        for sentence in _SENTENCE.split(para):
            if estimate_tokens(sentence) <= max_tokens:
                out.append(sentence)
            else:
                words, buf = sentence.split(), []
                for word in words:
                    buf.append(word)
                    if estimate_tokens(" ".join(buf)) >= max_tokens:
                        out.append(" ".join(buf))
                        buf = []
                if buf:
                    out.append(" ".join(buf))
    return out


def _make_chunk(index: int, header: str, section: str | None, pieces: list[str], filename: str) -> Chunk:
    content = header + "\n\n".join(pieces)
    return Chunk(
        index=index,
        content=content,
        section=section,
        token_count=estimate_tokens(content),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        metadata={"filename": filename, "section": section},
    )


def chunk_document(text: str, *, filename: str, max_tokens: int = 350) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section, body in _split_sections(text.replace("\r\n", "\n")):
        header = f"[{section}]\n" if section else ""
        budget = max(40, max_tokens - estimate_tokens(header))
        buffer: list[str] = []
        for piece in _pieces(body, budget):
            if buffer and estimate_tokens("\n\n".join([*buffer, piece])) > budget:
                chunks.append(_make_chunk(len(chunks), header, section, buffer, filename))
                buffer = []
            buffer.append(piece)
        if buffer:
            chunks.append(_make_chunk(len(chunks), header, section, buffer, filename))
    return chunks
