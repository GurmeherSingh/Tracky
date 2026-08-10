from tracker.classify.llm import HARD_TAIL_MODEL

MODEL = HARD_TAIL_MODEL
# the 2026 variant filters results server-side; declaring code_execution
# alongside it conflicts with that filtering, so search is the only tool here
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search",
                   "max_uses": 8}
MAX_TOKENS = 8000
MAX_RESUMES = 3

SYSTEM_PROMPT = """You research a company for someone who is about to \
interview there. Your output is pasted into their notes page as-is.

Rules:
- Every claim about the company must come from a search result you actually \
retrieved. If a section has nothing behind it, write "Nothing found." Never \
fill a gap from memory.
- Date every item under Recent news (YYYY-MM at minimum). An undated item is \
not news and must be dropped.
- For the interview process, report only what candidates or the company have \
actually published, and say which. Do not generalise from other companies.
- Prefer the last 12 months. Older material only if it is still the company's \
own current framing (values, mission).

Formatting — the output is converted to notes blocks by a parser that \
understands exactly this much:
- `## ` and `### ` headings
- `- ` bullets
- `[label](url)` links
- plain paragraphs
Anything else (bold, tables, code fences, numbered lists, nested bullets) is \
rendered literally and looks broken. Do not use it.

Write no preamble and no sign-off. The first characters of your answer are \
"## What they do". Emit these sections in this order, nothing before or after:

## What they do
## What they say they value
## Recent news & posts
## Interview process
## Questions worth asking
## Sources
"""


class BriefingFailed(Exception):
    pass


def _user_message(company: str, role: str) -> str:
    line = f"Company: {company}"
    if role:
        line += f"\nRole being interviewed for: {role}"
    return line + "\n\nResearch this company and write the briefing."


def _call(client, messages):
    # streamed: opus plus a multi-search server loop routinely outlives the
    # non-streaming request timeout
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        tools=[WEB_SEARCH_TOOL],
        messages=messages,
    ) as stream:
        return stream.get_final_message()


def _search_error(message) -> str | None:
    """A failed server-side search arrives as a successful HTTP 200 whose
    result block holds an error object where a list of results should be."""
    for block in message.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        if not isinstance(block.content, list):
            return getattr(block.content, "error_code", "unknown")
    return None


def _text_of(message) -> str:
    # newline, not "": the model's prose is split into a separate text block
    # either side of every search, and concatenating welds the tail of one onto
    # the head of the next — enough to turn a heading into body text
    return "\n".join(b.text.strip() for b in message.content
                     if getattr(b, "type", None) == "text" and b.text.strip())


def _from_first_heading(markdown: str) -> str:
    """Drop any conversational preamble ahead of the first section.

    Deterministic rather than trusting the prompt: the page must open on a
    heading, and "Sure, researching now." would otherwise be its first line.
    """
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[i:])
    return markdown


def build_briefing(client, company: str, role: str = "") -> str:
    """Research `company` with server-side web search and return markdown.

    Raises BriefingFailed rather than returning a partial briefing — a
    truncated one reads as complete, which is worse than none at all.
    """
    messages = [{"role": "user", "content": _user_message(company, role)}]
    chunks: list[str] = []
    for _ in range(MAX_RESUMES + 1):
        message = _call(client, messages)
        error = _search_error(message)
        if error:
            raise BriefingFailed(f"web_search: {error}")
        chunks.append(_text_of(message))
        if message.stop_reason != "pause_turn":
            markdown = "\n".join(c for c in chunks if c.strip()).strip()
            if not markdown:
                raise BriefingFailed("model returned an empty briefing")
            return _from_first_heading(markdown)
        # the server hit its search-iteration cap mid-answer; handing the
        # partial turn back is what lets it carry on
        messages.append({"role": "assistant", "content": message.content})
    raise BriefingFailed(f"search did not converge after {MAX_RESUMES} resumes")
