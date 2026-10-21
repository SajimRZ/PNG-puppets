#!/usr/bin/env python3
"""Low-resource local TTS powered by Piper.

This script is intentionally lightweight and easy to run on CPU-only machines.
It uses the small ONNX voice models from the Piper project, which are typically
much lighter than full generative speech models.

Features:
- tiny local dependency footprint for a decent-quality voice
- CPU-friendly execution (`piper-tts` + ONNX runtime)
- simple CLI with optional voice, speed, pitch, and volume controls
- plays audio immediately by default without leaving a WAV file behind

Examples:
  python voice-system/tts.py --text "Hello there, friend." --voice en_US-lessac-medium --play
  python voice-system/tts.py --text-file prompts.txt --output speech.wav --speed 1.25 --pitch 1.1
  python voice-system/tts.py --list-voices
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

try:
    from piper import PiperVoice, SynthesisConfig
    from piper.download_voices import download_voice
except ImportError as exc:  # pragma: no cover - user setup message
    print(
        "[TTS] Missing dependency: 'piper-tts'.\n"
        "Install it with: python -m pip install piper-tts",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(2) from exc

# ---- Change these defaults to configure the TTS for the whole project -------
DEFAULT_VOICE = "en_US-lessac-medium"
DEFAULT_SPEED = 1.2
DEFAULT_PITCH = 1.05
DEFAULT_VOLUME = 1.0
DEFAULT_SPEAKER = 0
DEFAULT_NORMALIZE_AUDIO = True
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models"
# ------------------------------------------------------------------------------

_voice_cache: dict[tuple[str, Path], PiperVoice] = {}


def _load_voice(voice_name: str, model_dir: Path) -> PiperVoice:
    cache_key = (voice_name, model_dir)
    if cache_key not in _voice_cache:
        model_path, config_path = ensure_voice_model(voice_name, model_dir)
        _voice_cache[cache_key] = PiperVoice.load(
            model_path=model_path,
            config_path=config_path,
            use_cuda=False,
            download_dir=model_dir,
        )
    return _voice_cache[cache_key]


def _make_synthesis_config(
    speed: float,
    pitch: float,
    volume: float,
    speaker: int,
    normalize_audio: bool,
) -> SynthesisConfig:
    if speed <= 0 or pitch <= 0 or volume <= 0:
        raise ValueError("speed, pitch, and volume must all be greater than zero.")
    return SynthesisConfig(
        speaker_id=speaker,
        length_scale=1.0 / speed,
        noise_scale=pitch,
        noise_w_scale=pitch * 0.7,
        normalize_audio=normalize_audio,
        volume=volume,
    )


def speak(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
    pitch: float = DEFAULT_PITCH,
    volume: float = DEFAULT_VOLUME,
    speaker: int = DEFAULT_SPEAKER,
    normalize_audio: bool = DEFAULT_NORMALIZE_AUDIO,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> None:
    """Synthesize and play one string, blocking until playback finishes.

    This is the public API for other Python files:

        from tts import speak
        speak("Hello from another file")

    No WAV file is created. The Piper model is cached after the first call.
    """
    if not text or not text.strip():
        raise ValueError("text must contain at least one non-whitespace character.")

    model_dir = Path(model_dir).resolve()
    loaded_voice = _load_voice(voice, model_dir)
    synthesis_config = _make_synthesis_config(
        speed=speed,
        pitch=pitch,
        volume=volume,
        speaker=speaker,
        normalize_audio=normalize_audio,
    )
    raw_audio, sample_rate, sample_width, channels = synthesize_text(
        loaded_voice,
        text.strip(),
        synthesis_config,
    )
    processed_audio = apply_audio_controls(
        raw_audio,
        sample_rate,
        sample_width,
        channels,
        speed=speed,
        pitch=pitch,
        volume=volume,
    )
    play_raw_audio(processed_audio, sample_rate)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Realtime local TTS using Piper. By default, it speaks immediately and does not save a WAV file."
    )
    parser.add_argument("--text", help="Text to speak immediately.")
    parser.add_argument(
        "--text-file",
        dest="text_file",
        help="Path to a UTF-8 text file. Each line is spoken in order.",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help=(
            "Piper voice name to use, e.g. 'en_US-lessac-medium'. "
            "A matching ONNX model is downloaded automatically if missing."
        ),
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Directory used for cached Piper voice files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional: save a WAV file to this path instead of speaking immediately.",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play the generated audio immediately. This is the default behavior when no output file is set.",
    )
    parser.add_argument(
        "--speaker",
        type=int,
        default=0,
        help="Speaker ID used by multi-speaker voices (default: 0).",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_SPEED,
        help=f"Speech speed multiplier. >1 is faster, <1 is slower (default: {DEFAULT_SPEED}).",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=DEFAULT_PITCH,
        help=f"Rough pitch-shift multiplier. >1 is higher, <1 is lower (default: {DEFAULT_PITCH}).",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=DEFAULT_VOLUME,
        help=f"Output gain multiplier (default: {DEFAULT_VOLUME}).",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable default audio normalization.",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List the available Piper voices and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show model download and synthesis debug logs.",
    )
    return parser.parse_args()


def list_available_voices() -> None:
    """Print the voice names published by the Piper voice set."""
    import json
    from urllib.request import urlopen

    voices_json = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
    )
    with urlopen(voices_json, timeout=30) as response:
        data = json.load(response)

    names = sorted(data.keys())
    if not names:
        print("[TTS] No voices were returned by the model catalog.")
        return

    print("Available Piper voices:")
    for name in names:
        print(f"  {name}")


def ensure_voice_model(voice_name: str, model_dir: Path) -> tuple[Path, Path]:
    """Download the voice model/config if they do not already exist."""
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{voice_name}.onnx"
    config_path = model_dir / f"{voice_name}.onnx.json"

    if not model_path.exists() or not config_path.exists():
        print(f"[TTS] Downloading voice '{voice_name}' into '{model_dir}'...", flush=True)
        download_voice(voice_name, model_dir)

    if not model_path.exists():
        raise FileNotFoundError(f"Voice model not found: {model_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Voice config not found: {config_path}")

    return model_path, config_path


def read_text_lines(args: argparse.Namespace) -> list[str]:
    if args.text:
        return [args.text]
    if args.text_file:
        text_file = Path(args.text_file)
        if not text_file.exists():
            raise FileNotFoundError(f"Text file not found: {text_file}")
        with text_file.open("r", encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    raise ValueError("No text input was provided. Use --text or --text-file.")


def synthesize_text(voice: PiperVoice, text: str, syn_config: SynthesisConfig) -> tuple[bytes, int, int, int]:
    """Return PCM bytes and metadata for the given text."""
    audio_chunks = list(voice.synthesize(text, syn_config=syn_config))
    if not audio_chunks:
        raise RuntimeError(f"No speech was generated for text: {text!r}")

    sample_rate = audio_chunks[0].sample_rate
    sample_width = audio_chunks[0].sample_width
    channels = audio_chunks[0].sample_channels
    raw_audio = b"".join(chunk.audio_int16_bytes for chunk in audio_chunks)
    return raw_audio, sample_rate, sample_width, channels


def _resample_pcm_1d(values: np.ndarray, target_len: int) -> np.ndarray:
    if target_len <= 1:
        return np.array([values[0]], dtype=values.dtype)
    if len(values) == 1:
        return np.full(target_len, values[0], dtype=values.dtype)

    old_x = np.arange(len(values), dtype=np.float32)
    new_x = np.linspace(0, len(values) - 1, target_len, dtype=np.float32)
    return np.interp(new_x, old_x, values).astype(values.dtype)


def _resample_pcm(data: np.ndarray, target_len: int) -> np.ndarray:
    if data.ndim == 1:
        return _resample_pcm_1d(data, target_len)

    return np.column_stack(
        [_resample_pcm_1d(data[:, idx], target_len) for idx in range(data.shape[1])]
    ).astype(data.dtype)


def apply_audio_controls(
    raw_audio: bytes,
    sample_rate: int,
    sample_width: int,
    channels: int,
    speed: float,
    pitch: float,
    volume: float,
) -> bytes:
    """Apply lightweight audio shaping so pitch and speed feel adjustable.

    We keep this dependency-light by using NumPy and simple waveform resampling.
    """
    if speed <= 0:
        raise ValueError("--speed must be greater than 0.")
    if pitch <= 0:
        raise ValueError("--pitch must be greater than 0.")
    if volume <= 0:
        raise ValueError("--volume must be greater than 0.")

    if sample_width == 1:
        dtype = np.uint8
        minimum = 0
        maximum = 255
        arr = np.frombuffer(raw_audio, dtype=dtype)
    elif sample_width == 2:
        dtype = np.int16
        minimum = -32768
        maximum = 32767
        arr = np.frombuffer(raw_audio, dtype=dtype)
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    if channels > 1:
        arr = arr.reshape(-1, channels)
    else:
        arr = arr.reshape(-1)

    if abs(speed - 1.0) > 1e-4:
        target_len = max(1, int(len(arr) / speed))
        arr = _resample_pcm(arr, target_len)

    if abs(pitch - 1.0) > 1e-4:
        target_len = max(1, int(len(arr) / pitch))
        arr = _resample_pcm(arr, target_len)

    if abs(volume - 1.0) > 1e-4:
        arr = np.clip(arr * volume, minimum, maximum).astype(dtype)

    if channels > 1:
        return arr.reshape(-1).tobytes()
    return arr.tobytes()


def write_wav_file(
    output_path: Path,
    raw_audio: bytes,
    sample_rate: int,
    sample_width: int,
    channels: int,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_audio)
    return output_path


def play_raw_audio(raw_audio: bytes, sample_rate: int) -> None:
    """Play PCM audio immediately without saving a WAV file to disk."""
    ffplay = shutil.which("ffplay")
    if ffplay:
        process = subprocess.Popen(
            [
                ffplay,
                "-nodisp",
                "-autoexit",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-i",
                "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        process.stdin.write(raw_audio)
        process.stdin.flush()
        process.stdin.close()
        process.wait(timeout=30)
        return

    if sys.platform.startswith("win"):
        import winsound

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(raw_audio)
        winsound.PlaySound(wav_buffer.getvalue(), winsound.SND_MEMORY)
        return

    player = shutil.which("aplay") or shutil.which("afplay")
    if player:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            wav_path = Path(temp_file.name)
        write_wav_file(wav_path, raw_audio, sample_rate, 2, 1)
        try:
            subprocess.run(
                [player, str(wav_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            wav_path.unlink(missing_ok=True)
        return

    raise RuntimeError("No local audio player is available for immediate playback.")


def main() -> None:
    args = parse_args()

    if args.debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    if args.list_voices:
        list_available_voices()
        return

    if not args.text and not args.text_file:
        raise SystemExit("Please provide text with --text or --text-file.")

    lines = read_text_lines(args)
    if not args.output:
        for line in lines:
            speak(
                line,
                voice=args.voice,
                speed=args.speed,
                pitch=args.pitch,
                volume=args.volume,
                speaker=args.speaker,
                normalize_audio=not args.no_normalize,
                model_dir=args.model_dir,
            )
        return

    model_dir = args.model_dir.resolve()
    voice = _load_voice(args.voice, model_dir)
    synth_config = _make_synthesis_config(
        speed=args.speed,
        pitch=args.pitch,
        volume=args.volume,
        speaker=args.speaker,
        normalize_audio=not args.no_normalize,
    )
    combined_audio = bytearray()
    sample_rate = 22050
    sample_width = 2
    channels = 1

    for line in lines:
        raw_audio, sample_rate, sample_width, channels = synthesize_text(voice, line, synth_config)
        processed = apply_audio_controls(
            raw_audio,
            sample_rate,
            sample_width,
            channels,
            speed=args.speed,
            pitch=args.pitch,
            volume=args.volume,
        )
        combined_audio.extend(processed)
        combined_audio.extend(b"\x00" * max(1, int(sample_rate * 0.05) * sample_width * channels))

    if combined_audio:
        # Remove the trailing silence gap added between utterances.
        if combined_audio.endswith(b"\x00" * max(1, int(sample_rate * 0.05) * sample_width * channels)):
            del combined_audio[-max(1, int(sample_rate * 0.05) * sample_width * channels):]

    if args.output:
        final_path = write_wav_file(args.output, bytes(combined_audio), sample_rate, sample_width, channels)
        print(f"[TTS] Saved WAV to: {final_path}", flush=True)
        if args.play:
            play_raw_audio(bytes(combined_audio), sample_rate)
        return

    play_raw_audio(bytes(combined_audio), sample_rate)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[TTS] Interrupted by user.", file=sys.stderr, flush=True)
    except Exception as exc:  # pragma: no cover - CLI failure reporting
        print(f"[TTS] Error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
