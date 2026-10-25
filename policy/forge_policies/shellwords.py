"""Split a shell line into the command segments a guard must judge. Stdlib only.

`expand` returns every segment of the line, with the payload of `bash -c`-style wrappers, `(...)`
subshells and `$(...)` / backtick substitutions lifted out as segments of their own. `ok` is False
when something could not be followed (unterminated quote or group, an unparsable wrapper payload,
the depth or segment bound): the caller then applies its blind handling instead of allowing.
"""
import os
import shlex

SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "busybox"})
MAX_DEPTH = 4
MAX_SEGMENTS = 64
_SEPARATORS = ";&|\n"


def _matching(text, start, open_ch, close_ch):
    """Index of the close_ch that matches text[start] == open_ch, or -1."""
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _wrapper_payload(tokens):
    """The `-c` payload of a known shell invoked anywhere in tokens, else None."""
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) not in SHELLS:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            cluster = tokens[j]
            j += 1
            if not cluster.startswith("--") and "c" in cluster[1:]:
                return tokens[j] if j < len(tokens) else ""
    return None


def expand(command, _depth=0):
    """Return (segments, ok) for a command line."""
    if _depth > MAX_DEPTH:
        return [command], False
    segments, ok = [], True
    buf, quote, i, n = [], None, 0, len(command)

    def flush():
        text = "".join(buf).strip()
        buf.clear()
        if text:
            segments.append(text)

    def lift(inner):
        nonlocal ok
        sub, sub_ok = expand(inner, _depth + 1)
        segments.extend(sub)
        ok = ok and sub_ok

    while i < n:
        ch = command[i]
        if ch == "\\" and quote != "'":
            buf.append(command[i:i + 2])
            i += 2
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch == "`":
            end = command.find("`", i + 1)
            if end < 0:
                ok = False
                buf.append(command[i:])
                break
            lift(command[i + 1:end])
            buf.append(" ")
            i = end + 1
            continue
        if ch == "(" and (quote is None or command[i - 1:i] == "$"):
            end = _matching(command, i, "(", ")")
            if end < 0:
                ok = False
                buf.append(command[i:])
                break
            if buf and buf[-1].endswith("$"):
                buf[-1] = buf[-1][:-1]
            lift(command[i + 1:end])
            buf.append(" ")
            i = end + 1
            continue
        if quote is None:
            if ch in "'\"":
                quote = ch
            elif ch in _SEPARATORS or ch == ")":
                flush()
                i += 1
                continue
        elif ch == quote:
            quote = None
        buf.append(ch)
        i += 1
    if quote is not None:
        ok = False
    flush()
    out = []
    for seg in segments:
        out.append(seg)
        try:
            payload = _wrapper_payload(shlex.split(seg))
        except ValueError:
            ok = False
            continue
        if payload:
            sub, sub_ok = expand(payload, _depth + 1)
            out.extend(sub)
            ok = ok and sub_ok
    if len(out) > MAX_SEGMENTS:
        return out[:MAX_SEGMENTS], False
    return out, ok
