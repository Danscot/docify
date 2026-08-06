"""
Shared streaming helper for all Docify pipeline steps.

Why streaming fixes timeouts:
- Batch mode: the HTTP connection stays open but silent until the model
  finishes generating ALL tokens. If the model takes 90s, the read timeout
  fires at 90s even though the model is still working.
- Streaming: the first token arrives in ~2s, resetting the read timeout
  on every chunk. A 16,000-token response that takes 120s never times out
  because bytes are flowing continuously.
"""
import logging
import time

log = logging.getLogger("docify.stream")


def stream_completion(
    client,
    model: str,
    messages: list,
    max_tokens: int = 16000,
    label: str = "API",
) -> str:
    """
    Stream a chat completion and return the full assembled text.

    Logs token progress every 10 seconds so you can watch generation
    in the log viewer without waiting for it to finish.
    """
    log.info("[%s] Streaming request — max_tokens=%d", label, max_tokens)
    t0         = time.time()
    chunks     = []
    last_log   = t0
    char_count = 0

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )

        for chunk in stream:
            # Extract text delta
            delta = ""
            if chunk.choices:
                delta = chunk.choices[0].delta.content or ""

            if delta:
                chunks.append(delta)
                char_count += len(delta)

            # Log progress every 10s
            now = time.time()
            if now - last_log >= 10:
                log.info("[%s] Streaming... %.0fs elapsed, %d chars received",
                         label, now - t0, char_count)
                last_log = now

            # Check finish reason
            if chunk.choices and chunk.choices[0].finish_reason:
                reason = chunk.choices[0].finish_reason
                if reason != "stop":
                    log.warning("[%s] finish_reason=%s — output may be truncated!", label, reason)
                break

    except Exception as e:
        elapsed = time.time() - t0
        log.error("[%s] Stream error after %.1fs (%d chars received): %s",
                  label, elapsed, char_count, e)
        # If we got partial content, still try to use it
        if chunks:
            log.warning("[%s] Returning partial content (%d chars)", label, char_count)
        else:
            raise

    elapsed = time.time() - t0
    full_text = "".join(chunks)
    log.info("[%s] Stream complete — %d chars in %.1fs (%.0f chars/s)",
             label, len(full_text), elapsed,
             len(full_text) / elapsed if elapsed > 0 else 0)
    return full_text
