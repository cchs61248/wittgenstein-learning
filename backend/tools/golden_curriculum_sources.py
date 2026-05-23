"""Golden curriculum regression sources — one book per content archetype."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GoldenSource:
    id: str
    archetype: str
    label: str
    candidate_paths: tuple[str, ...]
    min_chunks: int = 10
    min_section_titles: int = 0
    full_v2: bool = True
    notes: str = ""

    def resolve(self) -> Path | None:
        for raw in self.candidate_paths:
            p = Path(raw)
            if p.is_file():
                return p
        return None


# Paths: first existing wins. Add machine-specific paths as needed.
_DOWNLOADS = Path.home() / "Downloads"
_EPUB_BOOK = Path(r"C:\Users\dqaiot\Documents\aaron\epub\book")

GOLDEN_SOURCES: tuple[GoldenSource, ...] = (
    GoldenSource(
        id="api_design",
        archetype="tech_handbook",
        label="API Design.pdf",
        candidate_paths=(
            str(_DOWNLOADS / "API Design.pdf"),
            str(_EPUB_BOOK / "API Design.pdf"),
        ),
        min_chunks=3,
        min_section_titles=0,
        notes="Small-file tech handbook; V2 uses small_file path",
    ),
    GoldenSource(
        id="realtime_updates",
        archetype="case_series",
        label="Real-time Updates.pdf",
        candidate_paths=(
            str(_DOWNLOADS / "Real-time Updates 30f90cd3ccd8808092ace91c5e3f6c9c.pdf"),
            str(_DOWNLOADS / "Real-time Updates.pdf"),
            str(_EPUB_BOOK / "Real-time Updates.pdf"),
        ),
        min_chunks=20,
        min_section_titles=0,
        notes=(
            "Small-file IT case series; 5 protocol stages + framework/summary; "
            "global finalize recovers orphan chunks; stage1 Drift 100% regression"
        ),
    ),
    GoldenSource(
        id="qinzi_yingyu",
        archetype="framework_narrative",
        label="親子英語，玩出來.epub",
        candidate_paths=(
            str(_DOWNLOADS / "apk.tw_親子英語，玩出來.epub"),
        ),
        min_chunks=30,
        min_section_titles=20,
        notes="第X節 + 序言；敘述型 Drift 範例 I",
    ),
    GoldenSource(
        id="changqimaijin",
        archetype="parallel_lessons",
        label="長期買進.epub",
        candidate_paths=(
            str(_EPUB_BOOK / "長期買進.epub"),
            str(_DOWNLOADS / "長期買進.epub"),
        ),
        min_chunks=100,
        min_section_titles=20,
        notes="Part N + 第N堂；Plan B regression",
    ),
    GoldenSource(
        id="yiyuan_feiyang",
        archetype="listicle_rules",
        label="億元肥羊零成本買股術.epub",
        candidate_paths=(
            str(_DOWNLOADS / "億元肥羊零成本買股術 - 翁建原.epub"),
            str(_EPUB_BOOK / "億元肥羊零成本買股術 - 翁建原.epub"),
        ),
        min_chunks=80,
        min_section_titles=0,
        notes="法則 N listicle; titles often in body not section_title field",
    ),
)


def available_sources() -> list[tuple[GoldenSource, Path]]:
    out: list[tuple[GoldenSource, Path]] = []
    for spec in GOLDEN_SOURCES:
        path = spec.resolve()
        if path is not None:
            out.append((spec, path))
    return out
