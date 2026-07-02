# Bande Rythmo Local Studio

Application locale professionnelle pour generer une bande rythmo a partir d'une video.

## Lancement local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

Ouvrir ensuite : http://localhost:8000

## Fonctionnalites implementees

- Backend FastAPI local sur `localhost:8000`
- Base SQLite dans `data/bande_rythmo.sqlite3`
- Import video par projet
- Analyse FFprobe si FFmpeg est installe
- Extraction audio WAV 16 kHz pour ASR
- Transcription via `whisper.cpp` si configure, sinon transcription locale de demonstration
- Alignement temporel mot par mot par fallback deterministe
- Generation de bande rythmo sur canvas
- Lecture synchronisee video/bande
- Edition de mots et timings
- Exports JSON, XML, SRT, WebVTT, ASS et MP4 avec incrustation ASS via FFmpeg

## Configuration IA locale

L'application n'utilise aucun service cloud. Pour activer une transcription reelle avec `whisper.cpp` :

```powershell
$env:WHISPER_CPP_BIN = "C:\\chemin\\vers\\whisper-cli.exe"
$env:WHISPER_MODEL = "C:\\chemin\\vers\\modele.bin"
python -m app.main
```

`llama.cpp` est reserve aux enrichissements linguistiques futurs et doit rester local :

```powershell
$env:LLAMA_CPP_BIN = "C:\\chemin\\vers\\llama-cli.exe"
$env:LLAMA_MODEL = "C:\\chemin\\vers\\modele.gguf"
```

## Documentation produit

- [Conception complete](docs/CONCEPTION.md)

## Structure

```text
app/                 backend FastAPI, services, jobs, SQLite
frontend/            interface locale servie par FastAPI
data/                projets, base, exports et temporaires locaux
docs/                conception produit et technique
```
