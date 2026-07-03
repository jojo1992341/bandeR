from __future__ import annotations

import html
import json
from pathlib import Path

from app.config import settings
from app.core.utils import command_exists, json_dumps, run_command

PLAY_RES_X = 1920
PLAY_RES_Y = 1080
RYTHMO_Y = 870
PLAYHEAD_X = 690
PIXELS_PER_SECOND = 230
PRE_ROLL = 3.0
POST_ROLL = 3.0


def export_json(project: dict, transcript: dict, rythmo: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"project": project, "transcript": transcript, "rythmo": rythmo},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output


def export_xml(project: dict, transcript: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'<project id="{html.escape(project["id"])}" name="{html.escape(project["name"])}">']
    for segment in transcript.get("segments", []):
        lines.append(
            f'  <segment id="{segment["id"]}" start="{segment["start_time"]:.3f}" end="{segment["end_time"]:.3f}">'
        )
        lines.append(f"    <text>{html.escape(segment['text'])}</text>")
        for word in segment.get("words", []):
            lines.append(
                f'    <word id="{word["id"]}" start="{word["start_time"]:.3f}" end="{word["end_time"]:.3f}">'
                f"{html.escape(word['text'])}</word>"
            )
        lines.append("  </segment>")
    lines.append("</project>")
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def export_srt(transcript: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for idx, segment in enumerate(transcript.get("segments", []), start=1):
        blocks.append(
            "\n".join(
                [
                    str(idx),
                    f"{_srt_time(segment['start_time'])} --> {_srt_time(segment['end_time'])}",
                    segment["text"],
                ]
            )
        )
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return output


def export_vtt(transcript: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    blocks = ["WEBVTT", ""]
    for segment in transcript.get("segments", []):
        blocks.append(f"{_vtt_time(segment['start_time'])} --> {_vtt_time(segment['end_time'])}")
        blocks.append(segment["text"])
        blocks.append("")
    output.write_text("\n".join(blocks), encoding="utf-8")
    return output


def export_ass(transcript: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_build_rythmo_ass(transcript), encoding="utf-8")
    return output


def export_video_with_rythmo(source_video: Path, transcript: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not command_exists(settings.ffmpeg_bin):
        raise RuntimeError(f"FFmpeg introuvable: {settings.ffmpeg_bin}")

    duration = _transcript_duration(transcript)
    if duration <= 0:
        raise RuntimeError("Pas de contenu pour la bande rythmo")

    # Lire la taille de la vidéo source
    probe_args = [
        settings.ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_entries",
        "stream=width,height,codec_type",
        str(source_video),
    ]
    probe_result = run_command(probe_args, timeout=30)
    if probe_result.returncode != 0:
        raise RuntimeError(f"ffprobe a echoue: {probe_result.stderr}")
    try:
        probe_data = json.loads(probe_result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"ffprobe output invalide: {probe_result.stdout}")
    streams = probe_data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError("Aucune stream video trouvee")

    src_width = int(video_stream.get("width", 1920))
    src_height = int(video_stream.get("height", 1080))

    # Ajouter une bande de 180px en bas pour la rythmo
    RYTHMO_HEIGHT = 180
    dst_height = src_height + RYTHMO_HEIGHT

    # Calculer les filtres drawbox et drawtext pour chaque mot
    # Chaque mot a sa propre ligne (lane), defile de droite a gauche
    filter_parts = []

    # 1. Padding pour ajouter la bande rythmo en bas
    filter_parts.append(f"pad=w={src_width}:h={dst_height}:x=0:y={src_height}:color=black")

    # 2. Curseur fixe au centre de la bande rythmo (position X)
    # On dessine un rectangle rouge/orange comme curseur guide
    # Ce curseur reste fixe tandis que les mots defilent
    filter_parts.append(
        f"drawbox=x={PLAYHEAD_X-2}:y={src_height+12}:w=4:h={RYTHMO_HEIGHT-24}:color=yellow:thickness=2"
    )

    # 3. Mots de la bande rythmo
    # On reutilise la logique de _build_rythmo_ass pour determiner les lanes
    all_words = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            all_words.append(
                {
                    "text": word.get("text", ""),
                    "start": float(word.get("start_time", 0)),
                    "end": float(word.get("end_time", 0)),
                    "color": segment.get("speaker_color", "#2f80ed"),
                }
            )

    all_words.sort(key=lambda w: w["start"])

    # Assigner les lanes
    lane_free_at = [0.0] * RYTHMO_LANE_COUNT
    lane_y = [src_height + 40 + i * RYTHMO_LANE_HEIGHT for i in range(RYTHMO_LANE_COUNT)]

    for word in all_words:
        start = word["start"]
        end = word["end"]
        if end <= start:
            end = start + 0.12

        # Choisir la lane libre
        lane = 0
        for i in range(RYTHMO_LANE_COUNT):
            if lane_free_at[i] <= start + 0.5:
                lane = i
                break
        else:
            lane = min(range(RYTHMO_LANE_COUNT), key=lambda i: lane_free_at[i])
        lane_free_at[lane] = end

        y = lane_y[lane]
        text = _ass_escape(str(word["text"]))
        if not text:
            continue

        # Calcul du temps de debut et de fin d'affichage du mot
        show_start = max(0.0, start - PRE_ROLL)
        show_end = end + POST_ROLL

        # Largeur du bloc mot en pixels
        block_width = int((end - start) * PIXELS_PER_SECOND) + 20

        # Calculer start_x : position horizontale a t=0 (debut de la video)
        # A t=show_start, le mot doit etre a droite de l'ecran (hors cadre)
        # A t=start, le mot doit etre sur le curseur (PLAYHEAD_X)
        # A t=show_end, le mot doit etre a gauche de l'ecran (hors cadre)
        #
        # Equation lineaire: x(t) = start_x - t * PIXELS_PER_SECOND
        # A t=start: x = PLAYHEAD_X
        # Donc: start_x = PLAYHEAD_X + start * PIXELS_PER_SECOND
        #
        # Verification:
        # - A t=show_start: x = PLAYHEAD_X + start*PPS - show_start*PPS = PLAYHEAD_X + (start-show_start)*PPS
        #   = PLAYHEAD_X + PRE_ROLL * PPS = 690 + 3*230 = 1380 (debut hors cadre a droite) ✓
        # - A t=start: x = PLAYHEAD_X + start*PPS - start*PPS = PLAYHEAD_X ✓
        # - A t=show_end: x = PLAYHEAD_X + start*PPS - show_end*PPS = PLAYHEAD_X - (show_end-start)*PPS
        #   = PLAYHEAD_X - POST_ROLL * PPS = 690 - 3*230 = 0 (fin a gauche)
        start_x = int(PLAYHEAD_X + start * PIXELS_PER_SECOND)

        # Expression pour la position x en fonction du temps t (variable FFmpeg)
        # x(t) = start_x - t * PIXELS_PER_SECOND
        x_expr = f"{start_x}-t*{PIXELS_PER_SECOND}"

        # FFmpeg drawbox n'accepte pas le caractère "#". Il faut convertir
        # le format #RRGGBB en 0xRRGGBB
        hex_color = word["color"].replace("#", "0x")

        filter_parts.append(
            f"drawbox=x='{x_expr}':y={y-22}:w={block_width}:h=38:"
            f"color={hex_color}:thickness=fill"
        )

        # Drawtext : texte blanc
        # Echapper les apostrophes dans le texte pour FFmpeg (doubler les apostrophes)
        safe_text = text.replace("'", "''")
        filter_parts.append(
            f"drawtext=x='{x_expr}':y={y-3}:text='{safe_text}':fontcolor=white:fontsize=28:fontfile='Arial':shadowx=2:shadowy=2:shadowcolor=black@0.8"
        )

    filter_complex = ",".join(filter_parts)

    attempts = [
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(source_video),
            "-vf",
            filter_complex,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ],
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(source_video),
            "-vf",
            f"scale={min(1280, src_width)}:-2,{filter_complex}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output),
        ],
    ]

    errors = []
    for args in attempts:
        if output.exists():
            output.unlink()
        result = run_command(args, timeout=None)
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return output
        errors.append(_tail(result.stderr or result.stdout or "Export video echoue"))
    raise RuntimeError("Export video echoue apres deux tentatives.\n" + "\n---\n".join(errors))


def _fontfile_path() -> str:
    """Retourne le chemin d'un fichier de police disponible pour FFmpeg drawtext."""
    # FFmpeg drawtext peut utiliser des noms de police connus par le systeme.
    # Si Arial n'est pas trouvé, FFmpeg utilise une police par défaut.
    return "Arial"


def _build_rythmo_ass(transcript: dict) -> str:
    events = []
    duration = _transcript_duration(transcript)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Rythmo,Arial,46,&H00FFFFFF,&H0040D9A4,&H00111111,&HA0000000,1,0,1,3,0,5,0,0,0,1
Style: Guide,Arial,64,&H0040D9A4,&H0040D9A4,&H00111111,&H00000000,1,0,1,2,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""
    if duration > 0:
        events.append(
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration + POST_ROLL)},Guide,,0,0,0,,"
            f"{{\\pos({PLAYHEAD_X},{RYTHMO_Y})\\alpha&H20&}}|"
        )

    # Construire un mot par event, reparti sur 3 lanes. Chaque mot traverse
    # l'ecran de droite a gauche. On alterne les lanes sequentiellement pour
    # que deux mots consecutifs ne se chevauchent pas sur la meme ligne.
    all_words = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            all_words.append(
                {
                    "text": word.get("text", ""),
                    "start": float(word.get("start_time", 0)),
                    "end": float(word.get("end_time", 0)),
                    "color": segment.get("speaker_color", "#2f80ed"),
                }
            )

    # Trier par start_time pour un defilement chronologique
    all_words.sort(key=lambda w: w["start"])

    # Assigner les lanes
    lane_free_at = [0.0] * RYTHMO_LANE_COUNT
    lane_y = [src_height + 40 + i * RYTHMO_LANE_HEIGHT for i in range(RYTHMO_LANE_COUNT)]

    for word in all_words:
        start = word["start"]
        end = word["end"]
        if end <= start:
            end = start + 0.12

        # Choisir la lane libre
        lane = 0
        for i in range(RYTHMO_LANE_COUNT):
            if lane_free_at[i] <= start + 0.5:
                lane = i
                break
        else:
            lane = min(range(RYTHMO_LANE_COUNT), key=lambda i: lane_free_at[i])
        lane_free_at[lane] = end

        y = lane_y[lane]
        text = _ass_escape(str(word["text"]))
        if not text:
            continue

        # Calcul du temps de debut et de fin d'affichage du mot
        show_start = max(0.0, start - PRE_ROLL)
        show_end = end + POST_ROLL

        # Largeur du bloc mot en pixels
        block_width = int((end - start) * PIXELS_PER_SECOND) + 20

        # Calculer start_x : position horizontale a t=0 (debut de la video)
        # A t=show_start, le mot doit etre a droite de l'ecran (hors cadre)
        # A t=start, le mot doit etre sur le curseur (PLAYHEAD_X)
        # A t=show_end, le mot doit etre a gauche de l'ecran (hors cadre)
        #
        # Equation lineaire: x(t) = start_x - t * PIXELS_PER_SECOND
        # A t=start: x = PLAYHEAD_X
        # Donc: start_x = PLAYHEAD_X + start * PIXELS_PER_SECOND
        #
        # Verification:
        # - A t=show_start: x = PLAYHEAD_X + start*PPS - show_start*PPS = PLAYHEAD_X + (start-show_start)*PPS
        #   = PLAYHEAD_X + PRE_ROLL * PPS = 690 + 3*230 = 1380 (debut hors cadre a droite) ✓
        # - A t=start: x = PLAYHEAD_X + start*PPS - start*PPS = PLAYHEAD_X ✓
        # - A t=show_end: x = PLAYHEAD_X + start*PPS - show_end*PPS = PLAYHEAD_X - (show_end-start)*PPS
        #   = PLAYHEAD_X - POST_ROLL * PPS = 690 - 3*230 = 0 (fin a gauche)
        start_x = int(PLAYHEAD_X + start * PIXELS_PER_SECOND)

        # Drawbox : fond rectangulaire coloré (avec transparence via argb)
        # word['color'] est au format #RRGGBB, on le convertit en 0xAARRGGBB avec alpha=0.6
        hex_color = word["color"].replace("#", "")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        # alpha = 0.6 * 255 = 153 = 0x99
        a = 0x99
        argb_int = (a << 24) | (r << 16) | (g << 8) | b
        argb_hex = f"0x{{argb_int:08X}}".format(argb_int=argb_int)

        # Expression pour la position x en fonction du temps t (variable FFmpeg)
        # x(t) = start_x - t * PIXELS_PER_SECOND
        x_expr = f"{start_x}-t*{PIXELS_PER_SECOND}"

        filter_parts.append(
            f"drawbox=x='{x_expr}':y={y-22}:w={block_width}:h=38:"
            f"color={argb_hex}:thickness=fill"
        )

        # Drawtext : texte blanc
        filter_parts.append(
            f"drawtext=x='{x_expr}':y={y-3}:"
            f"text='{text}':fontcolor=white:fontsize=28:fontfile='{_fontfile_path()}':"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.8"
        )

    filter_complex = ",".join(filter_parts)

    attempts = [
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(source_video),
            "-vf",
            filter_complex,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ],
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(source_video),
            "-vf",
            f"scale={min(1280, src_width)}:-2,{filter_complex}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(output),
        ],
    ]

    errors = []
    for args in attempts:
        if output.exists():
            output.unlink()
        result = run_command(args, timeout=None)
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return output
        errors.append(_tail(result.stderr or result.stdout or "Export video echoue"))
    raise RuntimeError("Export video echoue apres deux tentatives.\n" + "\n---\n".join(errors))


def _fontfile_path() -> str:
    """Retourne le chemin d'un fichier de police disponible pour FFmpeg drawtext."""
    # FFmpeg drawtext peut utiliser des noms de police connus par le systeme.
    # Si Arial n'est pas trouvé, FFmpeg utilise une police par défaut.
    return "Arial"


def export_report(settings: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json_dumps(settings), encoding="utf-8")
    return output


RYTHMO_LANE_COUNT = 3
RYTHMO_LANE_HEIGHT = 50
RYTHMO_BASE_Y = RYTHMO_Y


def _build_rythmo_ass(transcript: dict) -> str:
    events = []
    duration = _transcript_duration(transcript)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Rythmo,Arial,46,&H00FFFFFF,&H0040D9A4,&H00111111,&HA0000000,1,0,1,3,0,5,0,0,0,1
Style: Guide,Arial,64,&H0040D9A4,&H0040D9A4,&H00111111,&H00000000,1,0,1,2,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""
    if duration > 0:
        events.append(
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration + POST_ROLL)},Guide,,0,0,0,,"
            f"{{\\pos({PLAYHEAD_X},{RYTHMO_Y})\\alpha&H20&}}|"
        )

    # Construire un mot par event, reparti sur 3 lanes. Chaque mot traverse
    # l'ecran de droite a gauche. On alterne les lanes sequentiellement pour
    # que deux mots consecutifs ne se chevauchent pas sur la meme ligne.
    all_words = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            all_words.append(
                {
                    "text": word.get("text", ""),
                    "start": float(word.get("start_time", 0)),
                    "end": float(word.get("end_time", 0)),
                }
            )

    # Trier par start_time pour un defilement chronologique
    all_words.sort(key=lambda w: w["start"])

    # Assigner les lanes : on utilise la plus ancienne lane libre
    lane_free_at = [0.0] * RYTHMO_LANE_COUNT
    for word in all_words:
        start = word["start"]
        end = word["end"]
        if end <= start:
            end = start + 0.12

        # Choisir la premiere lane dont le mot precedent est termine
        # avant le debut de ce mot (+ une petite marge de 0.15s)
        lane = 0
        for i in range(RYTHMO_LANE_COUNT):
            if lane_free_at[i] <= start + 0.5:
                lane = i
                break
        else:
            # Toutes occupees : prendre celle qui se libere le plus tot
            lane = min(range(RYTHMO_LANE_COUNT), key=lambda i: lane_free_at[i])

        lane_free_at[lane] = end

        event_start = max(0.0, start - PRE_ROLL)
        event_end = end + POST_ROLL
        x0 = int(PLAYHEAD_X + (start - event_start) * PIXELS_PER_SECOND)
        x1 = int(PLAYHEAD_X - (event_end - start) * PIXELS_PER_SECOND)
        y = RYTHMO_BASE_Y + lane * RYTHMO_LANE_HEIGHT

        text = _ass_escape(str(word["text"]))
        if not text:
            continue
        body = f"{{\\move({x0},{y},{x1},{y})\\fad(80,80)}}{text}"
        events.append(
            f"Dialogue: 1,{_ass_time(event_start)},{_ass_time(event_end)},Rythmo,,0,0,0,,{body}"
        )

    return header + "\n" + "\n".join(events) + "\n"


def _transcript_duration(transcript: dict) -> float:
    values = [float(segment.get("end_time", 0) or 0) for segment in transcript.get("segments", [])]
    return max(values) if values else 0.0


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "").replace("}", "").replace("\n", r"\N").strip()


def _srt_time(seconds: float) -> str:
    h, rem = divmod(float(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{int(h):02}:{int(m):02}:{int(s):02},{ms:03}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")


def _ass_time(seconds: float) -> str:
    h, rem = divmod(max(0.0, float(seconds)), 3600)
    m, s = divmod(rem, 60)
    cs = int(round((s - int(s)) * 100))
    return f"{int(h)}:{int(m):02}:{int(s):02}.{cs:02}"


def _ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace(":", r"\:").replace("'", r"\'")


def _tail(text: str, lines: int = 18) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
