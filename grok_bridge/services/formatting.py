"""Markdown из ответов Grok Bot → HTML-разметка Telegram.

Telegram-овский MarkdownV2 требует экранировать почти каждый символ, поэтому обычный
Markdown (как его пишут модели) переводится в безопасное подмножество HTML:
<b>, <i>, <s>, <code>, <pre>, <a>, <blockquote>. Если Telegram всё же не примет
разметку, вызывающий код отправляет исходный текст без форматирования.
"""

import html
import re

_FENCE_RE = re.compile(r"```([\w+#.-]*)[ \t]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+|tg://[^)\s]+|mailto:[^)\s]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*|__(?!\s)(.+?)(?<!\s)__", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])")
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)
_QUOTE_BLOCK_RE = re.compile(r"(?:^&gt;[ \t]?.*(?:\n|$))+", re.MULTILINE)

_PLACEHOLDER = "\x00{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")


def markdown_to_html(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return _PLACEHOLDER.format(len(stash) - 1)

    def fence(match: re.Match[str]) -> str:
        lang, code = match.group(1), match.group(2).rstrip("\n")
        attr = f' class="language-{html.escape(lang)}"' if lang else ""
        return keep(f"<pre><code{attr}>{html.escape(code, quote=False)}</code></pre>")

    text = text.replace("\x00", "")
    text = _FENCE_RE.sub(fence, text)
    text = _INLINE_CODE_RE.sub(lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK_RE.sub(
        lambda m: keep(f'<a href="{html.escape(m.group(2))}">') + m.group(1) + keep("</a>"), text
    )

    text = html.escape(text, quote=False)
    text = _QUOTE_BLOCK_RE.sub(_quote_block, text)
    text = _HEADER_RE.sub(r"<b>\1</b>", text)
    text = _BULLET_RE.sub(r"\1• ", text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", text)

    return _PLACEHOLDER_RE.sub(lambda m: stash[int(m.group(1))], text)


def _quote_block(match: re.Match[str]) -> str:
    block = match.group(0)
    trailing = "\n" if block.endswith("\n") else ""
    lines = [re.sub(r"^&gt;[ \t]?", "", line) for line in block.rstrip("\n").split("\n")]
    return "<blockquote>" + "\n".join(lines) + "</blockquote>" + trailing


def is_parse_error(error: Exception) -> bool:
    """TelegramBadRequest из-за разметки: «can't parse entities» и родственные."""
    message = str(error).lower()
    return "parse" in message and ("entit" in message or "tag" in message)
