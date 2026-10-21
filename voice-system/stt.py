#!/usr/bin/env python3
"""
Always-listening, streaming speech-to-text.

- Live partial text  -> stderr (so it never pollutes pipes)
- Final sentences    -> stdout (one per line, flushed immediately)
- F9                 -> kill switch (stops everything and exits)

Run:  python stt.py
Run on CPU: python stt.py --cpu
The separate chat process must be running on 127.0.0.1:8765.
"""
import argparse
import os
import socket
import sys

import keyboard
import torch
from RealtimeSTT import AudioToTextRecorder

KILL_KEY = "f9"
CHAT_HOST = "127.0.0.1"
CHAT_PORT = 8765

# ---- tweak these if you like -------------------------------------------------
MAIN_MODEL = "small.en"      # accurate model for final text (tiny.en / base.en / medium.en)
LIVE_MODEL = "tiny.en"       # fast model for the live partial text
ENABLE_PARTIAL_TRANSCRIPTION = False
USE_MAIN_MODEL_FOR_PARTIALS = False
USE_GPU = True               # override with --cpu/--gpu or STT_USE_GPU=0/1
GPU_DEVICE_INDEX = 0
GPU_COMPUTE_TYPE = "float16"
CPU_COMPUTE_TYPE = "int8"
MAIN_BATCH_SIZE = 4
LIVE_BATCH_SIZE = 4
LANGUAGE = "en"              # use a non-.en model (e.g. "small") for other languages
END_OF_SPEECH_SILENCE = 1.0  # seconds of silence before a sentence is "final"
SILERO_SENSITIVITY = 0.25   # lower this to reduce background-noise triggers
SILERO_DEACTIVITY_DETECTION = True
WEBRTC_SENSITIVITY = 3       # 3 is most resistant to weak/background sounds
# ------------------------------------------------------------------------------

recorder = None


def kill():
    print("\n[STT] F9 pressed - shutting down.", file=sys.stderr, flush=True)
    try:
        if recorder is not None:
            recorder.shutdown()
    finally:
        os._exit(0)  # hard exit so no background thread can keep it alive


def on_partial(text):
    # overwrite the same line in the terminal while you speak
    sys.stderr.write("\r\033[K" + text)
    sys.stderr.flush()


def on_transcription_start(_audio):
    print("[STT] Transcribing...", file=sys.stderr, flush=True)


def send_to_chat(text):
    """Send one finalized transcription to the chat process."""
    try:
        with socket.create_connection((CHAT_HOST, CHAT_PORT), timeout=1.0) as connection:
            connection.sendall((text.strip() + "\n").encode("utf-8"))
    except OSError as error:
        print(
            f"[STT] Could not send to chat at {CHAT_HOST}:{CHAT_PORT}: {error}",
            file=sys.stderr,
            flush=True,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Always-listening streaming speech-to-text.")
    device_group = parser.add_mutually_exclusive_group()
    device_group.add_argument("--gpu", action="store_true", help="use the GPU")
    device_group.add_argument("--cpu", action="store_true", help="use the CPU")
    return parser.parse_args()


def resolve_device(use_gpu_override):
    if use_gpu_override is None:
        configured_value = os.getenv("STT_USE_GPU")
        use_gpu = USE_GPU if configured_value is None else configured_value.lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    else:
        use_gpu = use_gpu_override

    if use_gpu:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "GPU mode was requested, but CUDA is not available. "
                "Run with --cpu or set STT_USE_GPU=0."
            )
        return "cuda", GPU_COMPUTE_TYPE

    return "cpu", CPU_COMPUTE_TYPE


if __name__ == "__main__":  # required: RealtimeSTT uses multiprocessing
    args = parse_args()
    use_gpu_override = True if args.gpu else False if args.cpu else None
    device, compute_type = resolve_device(use_gpu_override)
    print(
        f"[STT] Using {device.upper()} "
        f"(compute_type={compute_type})",
        file=sys.stderr,
        flush=True,
    )

    keyboard.add_hotkey(KILL_KEY, kill)

    recorder_options = {
        "model": MAIN_MODEL,
        "language": LANGUAGE,
        "compute_type": compute_type,
        "device": device,
        "gpu_device_index": GPU_DEVICE_INDEX,
        "batch_size": MAIN_BATCH_SIZE,
        "enable_realtime_transcription": ENABLE_PARTIAL_TRANSCRIPTION,
        "post_speech_silence_duration": END_OF_SPEECH_SILENCE,
        "silero_sensitivity": SILERO_SENSITIVITY,
        "silero_deactivity_detection": SILERO_DEACTIVITY_DETECTION,
        "webrtc_sensitivity": WEBRTC_SENSITIVITY,
        "spinner": False,
        "on_transcription_start": on_transcription_start,
    }
    if ENABLE_PARTIAL_TRANSCRIPTION:
        recorder_options.update(
            use_main_model_for_realtime=USE_MAIN_MODEL_FOR_PARTIALS,
            realtime_batch_size=LIVE_BATCH_SIZE,
            on_realtime_transcription_stabilized=on_partial,
        )
        if not USE_MAIN_MODEL_FOR_PARTIALS:
            recorder_options["realtime_model_type"] = LIVE_MODEL

    recorder = AudioToTextRecorder(**recorder_options)

    print(f"[STT] Listening... (press {KILL_KEY.upper()} to quit)", file=sys.stderr, flush=True)

    while True:
        final_text = recorder.text()      # blocks until you finish a sentence
        if final_text.strip():
            sys.stderr.write("\r\033[K")  # clear the partial line
            text = final_text.strip()
            print(text, flush=True)
            send_to_chat(text)