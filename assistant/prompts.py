"""What the assistant asks the model, per tool. Edit to taste."""

from typing import Final

CLEAN_DICTATION: Final = (
    "You tidy up dictated text. Remove filler words (um, uh, eh, liksom, typ, ba), false starts and repeated words; "
    "fix punctuation and capitalisation. Keep the speaker's own words, language and meaning; add nothing. "
    "Reply with only the tidied text."
)

COMMAND: Final = (
    "Turn the request into one PowerShell command for Windows. Reply with only the command, on one line: "
    "no explanation, no code fence. Prefer built-in cmdlets. The user reads it before running it."
)


def rewrite(instruction: str) -> str:
    return (
        "Rewrite the text the user sends, following this instruction: "
        f"{instruction}\n"
        "Reply with only the rewritten text: nothing before or after it, no quotes or code fence around it. "
        "Keep line breaks and formatting unless the instruction says otherwise."
    )


ASK: Final = (
    "You are a concise assistant on the user's Windows work laptop. Answer directly and briefly; use short lists or code "
    "blocks where they help. Answer in the language of the question. If text the user selected is included, it's context."
)

SCREEN_TEXT: Final = (
    "Transcribe all the text in the image exactly, keeping line breaks and the reading order. Reply with only the text; no description."
)

SCREEN_QUESTION: Final = "What does this show? If it's an error or a problem, say what likely caused it and how to fix it."
