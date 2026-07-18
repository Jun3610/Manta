"""
integrations/notion.py — Notion CRUD, 버그 토글 기록
"""
import re
import io
import discord

from manta_daemon.config import NOTION_PAGE_ID, NOTION_CODE_LANGS
import manta_daemon.state as state


def _make_notion_blocks(content):
    blocks = []
    pattern = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)
    last_end = 0
    for m in pattern.finditer(content):
        before = content[last_end:m.start()].strip()
        if before:
            for para in re.split(r'\n{2,}', before):
                para = para.strip()
                if para:
                    for chunk_start in range(0, len(para), 2000):
                        blocks.append({
                            "object": "block", "type": "paragraph",
                            "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[chunk_start:chunk_start+2000]}}]}
                        })
        lang = m.group(1).strip().lower() or "plain text"
        if lang not in NOTION_CODE_LANGS:
            lang = "plain text"
        code = m.group(2)
        for chunk_start in range(0, max(len(code), 1), 2000):
            blocks.append({
                "object": "block", "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": code[chunk_start:chunk_start+2000]}}],
                    "language": lang
                }
            })
        last_end = m.end()

    remaining = content[last_end:].strip()
    if remaining:
        for para in re.split(r'\n{2,}', remaining):
            para = para.strip()
            if para:
                for chunk_start in range(0, len(para), 2000):
                    blocks.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[chunk_start:chunk_start+2000]}}]}
                    })

    if not blocks:
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}
        })
    return blocks


def create_notion_page(title, content):
    if not state.notion:
        return "❌ NOTION 설정 누락."
    try:
        blocks = _make_notion_blocks(content)
        page = state.notion.pages.create(
            parent={"page_id": NOTION_PAGE_ID},
            properties={"title": {"title": [{"text": {"content": title[:2000]}}]}},
            children=blocks[:100]
        )
        for i in range(100, len(blocks), 90):
            state.notion.blocks.children.append(block_id=page["id"], children=blocks[i:i+90])
        return f"📝 노션에 `{title}` 페이지 작성 완료!"
    except Exception as e:
        return f"😥 노션 페이지 생성 오류: {e}"


def read_notion_page(page_id):
    from manta_daemon.utils.helpers import log_activity
    if not state.notion:
        return "❌ NOTION 설정 누락."
    try:
        page_info = state.notion.pages.retrieve(page_id=page_id)
        title_prop = page_info.get("properties", {}).get("title", {}).get("title", [])
        title = title_prop[0]["text"]["content"] if title_prop else "(제목 없음)"

        all_blocks = []
        cursor = None
        while True:
            kwargs = {"block_id": page_id}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = state.notion.blocks.children.list(**kwargs)
            all_blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        lines = [f"📄 **{title}**\n"]
        for block in all_blocks:
            btype = block.get("type", "")
            if btype == "code":
                lang = block["code"].get("language", "")
                code_text = "".join(rt.get("plain_text", "") for rt in block["code"].get("rich_text", []))
                lines.append(f"```{lang}\n{code_text}\n```")
            elif btype in ("heading_1", "heading_2", "heading_3"):
                rich_text = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_text)
                prefix = "#" * int(btype[-1])
                if text:
                    lines.append(f"{prefix} {text}")
            elif btype == "bulleted_list_item":
                rich_text = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_text)
                if text:
                    lines.append(f"• {text}")
            elif btype == "numbered_list_item":
                rich_text = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_text)
                if text:
                    lines.append(f"1. {text}")
            elif btype == "divider":
                lines.append("---")
            else:
                rich_text = block.get(btype, {}).get("rich_text", []) if isinstance(block.get(btype), dict) else []
                text = "".join(rt.get("plain_text", "") for rt in rich_text)
                if text:
                    lines.append(text)

        full_content = "\n".join(lines)

        state.current_context = {
            "type": "notion",
            "name": title,
            "content": full_content,
            "numbered_content": ""
        }
        log_activity("노션 읽기", f"'{title}' 컨텍스트 저장 완료")

        if len(full_content) > 1800:
            buf = io.BytesIO(full_content.encode('utf-8'))
            f = discord.File(fp=buf, filename=f"{title}.txt")
            return {"type": "file", "message": f"📄 `{title}` 내용 (파일로 드려요)", "file_object": f}
        return full_content
    except Exception as e:
        return f"😥 노션 페이지 읽기 오류: {e}"


def update_notion_page(page_id, new_title=None, new_content=None):
    if not state.notion:
        return "❌ NOTION 설정 누락."
    try:
        if new_title:
            state.notion.pages.update(page_id=page_id,
                                properties={"title": {"title": [{"text": {"content": new_title[:2000]}}]}})
        if new_content:
            cursor = None
            while True:
                kwargs = {"block_id": page_id}
                if cursor:
                    kwargs["start_cursor"] = cursor
                existing = state.notion.blocks.children.list(**kwargs)
                for block in existing.get("results", []):
                    try:
                        state.notion.blocks.delete(block_id=block["id"])
                    except Exception:
                        pass
                if not existing.get("has_more"):
                    break
                cursor = existing.get("next_cursor")

            blocks = _make_notion_blocks(new_content)
            for i in range(0, len(blocks), 90):
                state.notion.blocks.children.append(block_id=page_id, children=blocks[i:i+90])
        return "✏️ 노션 페이지 수정 완료!"
    except Exception as e:
        return f"😥 노션 수정 오류: {e}"


def list_notion_subpages():
    if not state.notion:
        return None, "❌ NOTION 설정 누락."
    try:
        all_blocks = []
        cursor = None
        while True:
            kwargs = {"block_id": NOTION_PAGE_ID}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = state.notion.blocks.children.list(**kwargs)
            all_blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        pages = [
            {"id": b["id"], "title": b["child_page"].get("title", "(제목 없음)")}
            for b in all_blocks if b.get("type") == "child_page"
        ]
        if not pages:
            return [], "📭 하위 페이지가 없어요."
        return pages, None
    except Exception as e:
        return None, f"😥 노션 목록 조회 오류: {e}"


def delete_notion_page(page_id):
    if not state.notion:
        return "error:NOTION 설정 누락"
    try:
        state.notion.pages.update(page_id=page_id, archived=True)
        return "success"
    except Exception as e:
        return f"error:{e}"


def append_to_notion_page(page_id, content):
    """기존 노션 페이지 끝에 내용 추가 (기존 내용 유지)"""
    if not state.notion:
        return "❌ NOTION 설정 누락."
    try:
        blocks = _make_notion_blocks(content)
        for i in range(0, len(blocks), 90):
            state.notion.blocks.children.append(block_id=page_id, children=blocks[i:i+90])
        return "✏️ 노션 페이지에 내용 추가 완료!"
    except Exception as e:
        return f"😥 노션 내용 추가 오류: {e}"


def _get_next_bug_info() -> tuple:
    """노션 페이지에서 'N차 버그' 최고 번호 + 해당 블록 ID 반환 → (n+1, last_block_id)"""
    if not state.notion or not NOTION_PAGE_ID:
        return 1, None
    try:
        import re as _re
        resp = state.notion.blocks.children.list(block_id=NOTION_PAGE_ID)
        max_n = 0
        last_bug_block_id = None
        for block in resp.get("results", []):
            btype = block.get("type", "")
            rich = block.get(btype, {}).get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            m = _re.search(r"(\d+)차\s*버그", text)
            if m:
                n = int(m.group(1))
                if n > max_n:
                    max_n = n
                    last_bug_block_id = block.get("id")
        return max_n + 1, last_bug_block_id
    except Exception:
        return 1, None


def _create_bug_toggle_in_notion(n: int, last_block_id, summary: str, commits: list) -> str:
    """노션에 'N차 버그' 토글을 마지막 버그 토글 바로 뒤에 삽입"""
    from datetime import datetime
    if not state.notion or not NOTION_PAGE_ID:
        return "❌ 노션 설정 없음"
    try:
        date_str = datetime.now().strftime("%Y년 %m월 %d일")
        commit_lines = "\n".join(f"• {c}" for c in commits) if commits else "커밋 없음"
        body = f"{summary}\n\n[커밋 내역]\n{commit_lines}"

        toggle_block = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": f"{n}차 버그"}}],
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": f"📅 {date_str}"}}]
                        }
                    },
                    {
                        "object": "block",
                        "type": "code",
                        "code": {
                            "rich_text": [{"type": "text", "text": {"content": body}}],
                            "language": "plain text"
                        }
                    }
                ]
            }
        }

        kwargs = {"block_id": NOTION_PAGE_ID, "children": [toggle_block]}
        if last_block_id:
            kwargs["after"] = last_block_id
        state.notion.blocks.children.append(**kwargs)
        return f"✅ 노션에 **{n}차 버그** 토글 추가 완료!"
    except Exception as e:
        return f"❌ 노션 기록 실패: {e}"
