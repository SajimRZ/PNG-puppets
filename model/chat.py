import argparse
import os
import queue
import socket
import sys
import threading
from typing import List, Dict

from llama_cpp import Llama


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

# Change this path to select the GGUF model.
MODEL_TO_RUN = "./gguf/lumi_v1.gguf"

# Matches the template used during Lumi training:
#   <|im_start|>user
#   message<|im_end|>
#
#   <|im_start|>assistant
#   <mood>...</mood>
#   <reply>...</reply><|im_end|>
#
# No system message is added.
IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
VOICE_HOST = "127.0.0.1"
VOICE_PORT = 8765


parser = argparse.ArgumentParser(
    description="Run local Lumi GGUF chat using Lumi's custom ChatML template."
)
parser.add_argument(
    "--model-path",
    default=MODEL_TO_RUN,
    help="Path to the GGUF model file.",
)
parser.add_argument(
    "--n-ctx",
    type=int,
    default=4096,
    help="Context window size in tokens. Must fit the GGUF model's supported context.",
)
parser.add_argument(
    "--n-gpu-layers",
    type=int,
    default=-1,
    help="Number of model layers to offload to the GPU. Use -1 for all layers.",
)
parser.add_argument(
    "--max-new-tokens",
    type=int,
    default=128,
    help="Maximum tokens to generate per reply.",
)
parser.add_argument(
    "--temperature",
    type=float,
    default=0.8,
    help="Sampling temperature; use 0 for greedy decoding.",
)
parser.add_argument(
    "--top-p",
    type=float,
    default=0.92,
    help="Nucleus sampling top-p.",
)
parser.add_argument(
    "--repetition-penalty",
    type=float,
    default=1.05,
    help="Penalty for repeated tokens.",
)
parser.add_argument(
    "--max-history-turns",
    type=int,
    default=6,
    help="Number of recent user+assistant turns to keep. Use 0 to disable turn-count trimming.",
)
parser.add_argument(
    "--max-input-tokens",
    type=int,
    default=3072,
    help="Maximum prompt tokens, including template markers. Older complete turns are removed to fit.",
)
parser.add_argument(
    "--seed",
    type=int,
    default=-1,
    help="Sampling seed. -1 lets the backend choose a seed.",
)
args = parser.parse_args()


def format_messages(messages: List[Dict[str, str]], add_generation_prompt: bool = True) -> str:
    """Render messages using the exact simple template used during training."""
    rendered = ""
    for message in messages:
        role = message["role"]
        content = message["content"]
        rendered += f"{IM_START}{role}\n{content}{IM_END}\n"

    if add_generation_prompt:
        rendered += f"{IM_START}assistant\n"

    return rendered


def trim_history(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep recent complete user/assistant turns, starting with a user message."""
    if args.max_history_turns <= 0:
        return messages

    # This is called both before generation (ending in a user message) and
    # after generation (ending in an assistant message).
    max_messages = args.max_history_turns * 2
    messages = messages[-max_messages:]

    # Avoid beginning the prompt with an assistant message.
    while messages and messages[0]["role"] != "user":
        messages = messages[1:]

    return messages


def encode_prompt(messages: List[Dict[str, str]]) -> List[int]:
    """Tokenize the manually rendered template, recognizing special tokens."""
    prompt = format_messages(messages, add_generation_prompt=True)
    return llm.tokenize(
        prompt.encode("utf-8"),
        add_bos=False,
        special=True,
    )


def fit_prompt_to_budget(messages: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], List[int]]:
    """
    Remove oldest complete exchanges until the prompt fits the input budget.
    The newest user message is preserved even if it alone exceeds the budget.
    """
    working = list(messages)

    while True:
        tokens = encode_prompt(working)
        if len(tokens) <= args.max_input_tokens:
            return working, tokens

        # Keep the latest user message and remove the oldest complete exchange.
        if len(working) <= 2:
            print(
                f"\nWarning: the newest exchange needs {len(tokens)} prompt tokens, "
                f"exceeding --max-input-tokens={args.max_input_tokens}. "
                "Consider increasing the input budget or shortening the message."
            )
            return working, tokens

        # Drop from the front through the next user message, so we don't leave
        # the history beginning with an orphaned assistant response.
        first_next_user = next(
            (i for i, msg in enumerate(working[1:], start=1) if msg["role"] == "user"),
            None,
        )
        if first_next_user is None:
            # Defensive fallback: retain the latest user message.
            working = working[-1:]
        else:
            working = working[first_next_user:]


def stream_chat_reply(messages):
    fitted_messages, prompt_tokens = fit_prompt_to_budget(messages)

    if len(prompt_tokens) >= args.n_ctx:
        raise ValueError(
            f"Prompt is {len(prompt_tokens)} tokens but --n-ctx is {args.n_ctx}. "
            "Increase --n-ctx or lower --max-input-tokens."
        )

    remaining_context = args.n_ctx - len(prompt_tokens)
    max_tokens = min(args.max_new_tokens, remaining_context)

    # Passing token IDs avoids re-tokenizing the manually rendered template
    # as ordinary text. The template's control tokens are tokenized as special.
    stream = llm.create_completion(
        prompt=prompt_tokens,
        max_tokens=max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        repeat_penalty=args.repetition_penalty,
        seed=None if args.seed < 0 else args.seed,
        stop=[IM_END],
        stream=True,
        echo=False,
    )

    for chunk in stream:
        text = chunk["choices"][0].get("text", "")
        if text:
            yield text


def process_input(
    history: List[Dict[str, str]], user_input: str
) -> tuple[List[Dict[str, str]], bool]:
    """Generate one reply and return the updated history and continue flag."""
    if not user_input:
        return history, True
    if user_input.lower() in {"exit", "quit", "/exit", "/quit"}:
        return history, False

    history.append({"role": "user", "content": user_input})
    history = trim_history(history)

    print("Assistant: ", end="", flush=True)
    assistant_reply = ""

    try:
        for chunk in stream_chat_reply(history):
            print(chunk, end="", flush=True)
            assistant_reply += chunk
    except KeyboardInterrupt:
        print("\n[Generation interrupted]")
        # Keep only generated text if any; the next prompt remains usable.
    except Exception as exc:
        print(f"\n[Generation error: {exc}]")
        # Remove the unanswered user message so a failed request doesn't
        # leave history ending with two consecutive user messages.
        if history and history[-1]["role"] == "user":
            history.pop()
        return history, True

    print()
    assistant_reply = assistant_reply.strip()
    if assistant_reply:
        history.append({"role": "assistant", "content": assistant_reply})

    history = trim_history(history)
    return history, True


def start_voice_listener(
    input_queue: "queue.Queue[tuple[str, str]]",
) -> threading.Thread:
    """Receive finalized STT sentences from the separate voice process."""

    def listen() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((VOICE_HOST, VOICE_PORT))
            server.listen(1)
            print(
                f"[Voice] Listening on {VOICE_HOST}:{VOICE_PORT}",
                file=sys.stderr,
                flush=True,
            )
            while True:
                connection, _ = server.accept()
                with connection:
                    buffer = b""
                    while True:
                        data = connection.recv(4096)
                        if not data:
                            break
                        buffer += data
                        while b"\n" in buffer:
                            raw_line, buffer = buffer.split(b"\n", 1)
                            text = raw_line.decode("utf-8", errors="replace").strip()
                            if text:
                                input_queue.put(("voice", text))
                    if buffer.strip():
                        text = buffer.decode("utf-8", errors="replace").strip()
                        if text:
                            input_queue.put(("voice", text))

    listener = threading.Thread(target=listen, name="voice-listener", daemon=True)
    listener.start()
    return listener


def start_keyboard_reader(
    input_queue: "queue.Queue[tuple[str, str]]",
) -> threading.Thread:
    """Read keyboard input without blocking voice messages."""

    def read_input() -> None:
        while True:
            try:
                text = input("\nYou: ").strip()
            except EOFError:
                return
            except KeyboardInterrupt:
                input_queue.put(("keyboard", "/exit"))
                return
            input_queue.put(("keyboard", text))

    reader = threading.Thread(target=read_input, name="keyboard-reader", daemon=True)
    reader.start()
    return reader


def chat():
    history: List[Dict[str, str]] = []
    input_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()

    print("\n" + "=" * 60)
    print("Local Lumi GGUF Chat Session Started. Type 'exit' or 'quit' to quit.")
    print(f"Model: {os.path.abspath(args.model_path)}")
    print("Custom template: Lumi ChatML (no system message)")
    print("=" * 60)

    start_voice_listener(input_queue)
    if os.isatty(0):
        start_keyboard_reader(input_queue)
    else:
        print(
            "[Voice] No interactive keyboard detected; waiting for STT messages.",
            file=sys.stderr,
            flush=True,
        )
    while True:
        source, user_input = input_queue.get()
        if source == "voice":
            print(f"\nYou (voice): {user_input}", file=sys.stderr, flush=True)
        history, should_continue = process_input(history, user_input)
        if not should_continue:
            print("Exiting chat.")
            break


if __name__ == "__main__":
    if not os.path.isfile(args.model_path):
        raise FileNotFoundError(
            f"GGUF model not found: {os.path.abspath(args.model_path)}\n"
            "Set MODEL_TO_RUN in this script or pass --model-path."
        )

    print(f"Loading GGUF model: {os.path.abspath(args.model_path)}")
    print(f"Context size: {args.n_ctx}")
    print(f"GPU layers: {args.n_gpu_layers}")

    llm = Llama(
        model_path=args.model_path,
        n_ctx=args.n_ctx,
        n_gpu_layers=args.n_gpu_layers,
        seed=args.seed,
        verbose=False,
    )

    print("Model loaded successfully!")
    chat()
