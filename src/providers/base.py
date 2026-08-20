#!/usr/bin/env python3
"""
Provider Adapter Interface — Multi-provider data collection abstraction.

Setiap provider (TikTok, LinkedIn, YouTube, Reddit) harus implementasi
adaptor ini. Interface uniform memungkinkan pipeline untuk berurusan
dengan satu antarmuka meski sumber data berbeda-beda.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

from src.schema.canonical import Observation


class ProviderAdapter(ABC):
    """Antarmuka dasar untuk provider data sosial."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nama unik provider — gunakan di entity_id dan provenance."""

    @abstractmethod
    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """
        Kumpulkan data dari provider ini.

        Args:
            url: URL sumber (video, post, thread)
            **kwargs: opsi tambahan (max_comments, cursor, dst)

        Returns:
            List[Observation] — hasil kumpulan dalam schema kanonikal
        """

    @abstractmethod
    def probe(self, url: str) -> Dict:
        """
        Cek ketersediaan dan metadata sumber.

        Returns:
            Dict dengan keys: url, provider, accessible, metadata
        """