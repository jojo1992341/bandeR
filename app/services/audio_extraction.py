from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.core.utils import command_exists, run_command


def extract_audio_for_asr(video_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not command_exists(settings.ffmpeg_bin):
        raise RuntimeError("FFmpeg introuvable. Installez FFmpeg ou configurez FFMPEG_BIN.")

    wav_temp = output_path.with_suffix(".wav")

    # Extraction audio en WAV 16kHz mono sans re-encodage superflu.
    # -fflags +genpts+igndts avant -i : genere des PTS propres et ignore
    # les timestamps d'entree corrompus (frequent dans les MP4/AAC avec
    # edit lists ou priming delay).
    # -async 1 : corrige la derive audio/video en re-alignant les samples.
    args = [
        settings.ffmpeg_bin,
        "-fflags",
        "+genpts+igndts",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        "-f",
        "wav",
        "-async",
        "1",
        str(wav_temp),
    ]
    result = run_command(args, timeout=None)
    if result.returncode != 0 or not wav_temp.exists():
        raise RuntimeError(result.stderr.strip() or "Extraction audio echouee")
    return wav_temp