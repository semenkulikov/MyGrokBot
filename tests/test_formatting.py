import pytest

from grok_bridge.services.formatting import is_parse_error, markdown_to_html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**жирный**", "<b>жирный</b>"),
        ("__жирный__", "<b>жирный</b>"),
        ("*курсив* и _курсив_", "<i>курсив</i> и <i>курсив</i>"),
        ("~~нет~~", "<s>нет</s>"),
        ("# Заголовок", "<b>Заголовок</b>"),
        ("- пункт\n* пункт", "• пункт\n• пункт"),
        ("snake_case_name и 2*3*4", "snake_case_name и 2*3*4"),
        ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
        ("`x<y`", "<code>x&lt;y</code>"),
        ("```py\nif a < b:\n    **x**\n```", '<pre><code class="language-py">if a &lt; b:\n    **x**</code></pre>'),
        ("[сайт](https://e.com/?a=1&b=2)", '<a href="https://e.com/?a=1&amp;b=2">сайт</a>'),
        ("[плохая](javascript:alert)", "[плохая](javascript:alert)"),
        ("> цитата\n> вторая\nпосле", "<blockquote>цитата\nвторая</blockquote>\nпосле"),
        ("<script>", "&lt;script&gt;"),
    ],
)
def test_markdown_to_html(source: str, expected: str) -> None:
    assert markdown_to_html(source) == expected


def test_is_parse_error() -> None:
    assert is_parse_error(Exception("Bad Request: can't parse entities: unsupported start tag"))
    assert not is_parse_error(Exception("Bad Request: message is too long"))
