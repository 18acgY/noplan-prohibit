"""把 AI 报告的 Markdown 转成可读 HTML。

- 默认不解析内联/块级 HTML，源码里的 `<script>` 会被转义；
- 链接按 CommonMark 校验，`javascript:` 之类不会生成可点击链接；
- 表格、加粗、列表、标题都按 GFM/CommonMark 渲染。
"""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("default", {"html": False, "linkify": False, "typographer": False})


def render_markdown(text: str | None) -> str:
    return _md.render(text or "")