"""Placeholder interfaces for the future wildprint poster generation engine.

Purpose
-------
This module defines the seams between the wildprint generation pipeline and
the (yet-to-be-built) poster renderer. It contains no rendering logic. Real
implementations land in a future milestone.

Relative scale
--------------
A LayoutEngine implementation MUST consult ``SpeciesRef.relative_scale_index``
when computing per-species draw sizes. That index is style-agnostic: a
Northern Pike with ``relative_scale_index = 2.5`` should render approximately
2.5x the body length of a Smallmouth Bass (``1.0``) regardless of how many
pixels each source master happens to occupy.

Master image flow
-----------------
Master images live at::

    output/master/{style_slug}/{species_slug}.png

A ``MasterImageLoader`` is responsible for resolving a ``(species_slug,
style_slug)`` pair to a ``MasterImage`` record (path + pixel dimensions).
The poster engine never reaches into the generation pipeline directly.

Decoupling
----------
The layout engine knows nothing about how master images were generated —
which provider, which prompt, how many variations, which one was selected.
It only consumes ``MasterImage`` objects via the loader, plus the structured
``SpeciesRef`` / ``StyleRef`` / ``PosterSpec`` data classes from
``poster_layout.models``.
"""
from __future__ import annotations


from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable

from poster_layout.models import (
    LayoutResult,
    MasterImage,
    PosterSpec,
    SpeciesRef,
)


@runtime_checkable
class MasterImageLoader(Protocol):
    """Lookup interface for master images by (species_slug, style_slug)."""

    def get(self, species_slug: str, style_slug: str) -> MasterImage:
        """Return the MasterImage for the given pair, or raise KeyError."""
        ...

    def exists(self, species_slug: str, style_slug: str) -> bool:
        """Return True if a master exists on disk for the given pair."""
        ...


class LayoutEngine(ABC):
    """Computes ``PlacedItem`` positions for a ``PosterSpec``.

    Implementations MUST use ``species_ref.relative_scale_index`` to drive
    relative draw sizes so that, for example, a Northern Pike renders
    ~2.5x the length of a Smallmouth Bass regardless of the source image's
    pixel dimensions. The pixel dimensions of a ``MasterImage`` describe
    only the asset; the relative scale index describes the species.
    """

    @abstractmethod
    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        """Compute placements for ``spec`` and return a ``LayoutResult``."""
        raise NotImplementedError


class PosterRenderer(ABC):
    """Renders a computed ``LayoutResult`` to an output file."""

    @abstractmethod
    def render(self, result: LayoutResult, output_path: Path) -> None:
        """Render the laid-out poster to ``output_path``."""
        raise NotImplementedError


class NotImplementedLayoutEngine(LayoutEngine):
    """Explicit stub that fails loudly until the real engine ships."""

    def layout(
        self,
        spec: PosterSpec,
        species: list[SpeciesRef],
        loader: MasterImageLoader,
    ) -> LayoutResult:
        raise NotImplementedError(
            "poster_layout is a placeholder — implement in a future milestone"
        )


class NotImplementedPosterRenderer(PosterRenderer):
    """Explicit stub that fails loudly until the real renderer ships."""

    def render(self, result: LayoutResult, output_path: Path) -> None:
        raise NotImplementedError(
            "poster_layout is a placeholder — implement in a future milestone"
        )
