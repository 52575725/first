#!/usr/bin/env python3
"""Audio generation optimizer with caching and parallel processing."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional


CACHE_DIR = Path.home() / ".ppt-master" / "audio_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_cache_key(text: str, voice: str, rate: str, provider: str = "edge") -> str:
    """Generate cache key for audio."""
    content = f"{provider}|{voice}|{rate}|{text}"
    return hashlib.md5(content.encode()).hexdigest()


def get_cached_audio(cache_key: str) -> Optional[Path]:
    """Check if cached audio exists."""
    cache_path = CACHE_DIR / f"{cache_key}.mp3"
    return cache_path if cache_path.exists() else None


def save_to_cache(audio_path: Path, cache_key: str) -> None:
    """Save audio to cache."""
    cache_path = CACHE_DIR / f"{cache_key}.mp3"
    if audio_path.exists():
        import shutil
        shutil.copy2(audio_path, cache_path)


async def generate_audio_with_cache(
    text: str,
    output_path: Path,
    voice: str,
    rate: str = "+0%",
    provider: str = "edge"
) -> bool:
    """Generate audio with caching support."""
    cache_key = get_cache_key(text, voice, rate, provider)
    cached = get_cached_audio(cache_key)

    if cached:
        import shutil
        shutil.copy2(cached, output_path)
        return True

    # Generate new audio (placeholder - integrate with notes_to_audio.py)
    # In real implementation, call the actual TTS backend
    success = False  # Replace with actual generation

    if success:
        save_to_cache(output_path, cache_key)

    return success


def clear_cache(max_size_mb: int = 500) -> None:
    """Clear old cache files if cache size exceeds limit."""
    cache_files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    total_size = sum(f.stat().st_size for f in cache_files) / 1024 / 1024

    if total_size > max_size_mb:
        # Remove oldest files
        removed_size = 0
        for f in cache_files:
            if total_size - removed_size < max_size_mb * 0.8:
                break
            removed_size += f.stat().st_size / 1024 / 1024
            f.unlink()


if __name__ == "__main__":
    print(f"Audio cache directory: {CACHE_DIR}")
    cache_files = list(CACHE_DIR.glob("*.mp3"))
    total_size = sum(f.stat().st_size for f in cache_files) / 1024 / 1024
    print(f"Cached files: {len(cache_files)}")
    print(f"Total size: {total_size:.1f} MB")
