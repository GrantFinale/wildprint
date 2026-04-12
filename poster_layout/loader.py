"""Filesystem-backed master image loader.

Resolves ``MasterImage`` records from the on-disk master hierarchy produced
by the wildprint generation pipeline:

    {masters_dir}/{style_slug}/{species_slug}.png

Instances cache resolved ``MasterImage`` objects in-memory so that repeated
lookups (e.g. during layout + render passes) are effectively free.
"""
from __future__ import annotations

from pathlib import Path

from config import settings
from poster_layout.models import MasterImage


class FileSystemMasterImageLoader:
    """Concrete ``MasterImageLoader`` backed by the local filesystem.

    Parameters
    ----------
    masters_dir:
        Root directory containing one subdirectory per style slug. Defaults
        to ``config.settings.MASTER_DIR``.
    """

    def __init__(self, masters_dir: Path | None = None) -> None:
        self.masters_dir: Path = Path(masters_dir) if masters_dir is not None else Path(settings.MASTER_DIR)
        self._cache: dict[tuple[str, str], MasterImage] = {}

    # ------------------------------------------------------------------ helpers

    def _path_for(self, species_slug: str, style_slug: str) -> Path:
        return self.masters_dir / style_slug / f"{species_slug}.png"

    # ------------------------------------------------------------------- loader

    def get(self, species_slug: str, style_slug: str) -> MasterImage:
        """Return the ``MasterImage`` for the given pair.

        Raises
        ------
        FileNotFoundError
            If no master image exists at the expected path.
        """
        key = (species_slug, style_slug)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = self._path_for(species_slug, style_slug)
        if not path.exists():
            raise FileNotFoundError(
                f"No master image found for species='{species_slug}' "
                f"style='{style_slug}' at {path}"
            )

        master = MasterImage.from_file(
            species_slug=species_slug,
            style_slug=style_slug,
            image_path=path,
        )
        self._cache[key] = master
        return master

    def exists(self, species_slug: str, style_slug: str) -> bool:
        """Return True if a master image exists on disk for the pair."""
        if (species_slug, style_slug) in self._cache:
            return True
        return self._path_for(species_slug, style_slug).exists()
