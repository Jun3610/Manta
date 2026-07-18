"""
tools/file_ops.py — 파일 탐색, 읽기, PDF, 코드 분석, 쓰기
"""
import os
import io
from datetime import datetime

import discord

from manta_daemon.config import (
    WORK_STATION_ROOT, HOME,
    _SEND_FILE_SEARCH_ROOTS, _MAX_SEND_FILE_SIZE,
    _EXT_KEYWORDS, _KO_HINT_MAP, _COMPILED_EXTS, _SOURCE_EXTS,
)
import manta_daemon.state as state
from manta_daemon.utils.helpers import log_activity


# ==================== [ 파일 읽기 헬퍼 ] ====================

def _add_line_numbers(content):
    lines = content.splitlines()
    return "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))


def _read_file_at_path(path):
    name = os.path.basename(path)
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                content = f.read()
            return {"name": name, "content": content, "numbered_content": _add_line_numbers(content)}, None
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return None, f"🚨 파일 읽기 오류: {e}"
    return None, f"🚨 인코딩 감지 실패: {name}"


def _normalize_hint(hint: str) -> str:
    """한국어 힌트를 영어 키워드로 정규화"""
    result = hint.lower()
    for ko, en in _KO_HINT_MAP.items():
        result = result.replace(ko, en)
    return result


def find_file(hint):
    hint_lower = _normalize_hint(hint.strip())
    folder_hint = None
    file_hint = hint_lower

    folder_keywords = ["폴더", "디렉토리", "folder", "directory", "프로젝트", "project"]
    for kw in folder_keywords:
        if kw in hint_lower:
            parts = hint_lower.split(kw)
            folder_hint = parts[0].strip()
            file_hint = parts[1].strip() if len(parts) > 1 else ""
            break

    EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.idea', 'build',
                    'dist', '.next', '.claude', 'out', 'target', '.gradle'}

    search_root = state.current_workspace["path"] if state.current_workspace else WORK_STATION_ROOT

    candidates = []
    for root, dirs, files in os.walk(search_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]

        if folder_hint:
            rel = os.path.relpath(root, search_root).lower()
            if not any(folder_hint in part for part in rel.split(os.sep)):
                continue

        for fname in files:
            if fname.startswith('.'):
                continue
            fname_lower = fname.lower()
            _, ext = os.path.splitext(fname_lower)
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, search_root).lower()

            score = 0

            if ext in _COMPILED_EXTS:
                score -= 8

            if ext in _SOURCE_EXTS:
                score += 3

            fname_no_ext = os.path.splitext(fname_lower)[0]
            if file_hint and file_hint in fname_lower:
                score += 10
            elif file_hint and file_hint in fname_no_ext:
                score += 9

            ext_map = {".py": ["python", ".py"], ".java": ["java", ".java"],
                       ".js": ["javascript", ".js"], ".ts": ["typescript", ".ts"],
                       ".kt": ["kotlin", ".kt"]}
            for file_ext, keywords in ext_map.items():
                if any(k in file_hint for k in keywords) and fname_lower.endswith(file_ext):
                    score += 5

            if file_hint and file_hint in rel_path:
                score += 3
            if folder_hint:
                score += 2

            if score > 0:
                candidates.append((score, full_path))

    if not candidates:
        return None, f"❌ '{hint}' 관련 파일을 work-station에서 찾지 못했어요."

    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1], None


def _parse_file_hint(hint: str):
    """hint에서 날짜 범위, 확장자 필터, 키워드 추출"""
    import re as _re
    from datetime import date as _date, timedelta as _td

    hint_l = hint.lower()
    now = datetime.now()
    today = _date.today()

    date_after = None
    if "오늘" in hint_l:
        date_after = today
    elif "어제" in hint_l:
        date_after = today - _td(days=1)
    elif "이번주" in hint_l or "이번 주" in hint_l:
        date_after = today - _td(days=today.weekday())
    elif "지난주" in hint_l or "저번주" in hint_l or "지난 주" in hint_l:
        date_after = today - _td(days=today.weekday() + 7)
    elif "이번달" in hint_l or "이번 달" in hint_l:
        date_after = today.replace(day=1)
    elif "지난달" in hint_l or "저번달" in hint_l:
        first = today.replace(day=1)
        date_after = (first - _td(days=1)).replace(day=1)
    elif "최근" in hint_l:
        m = _re.search(r"최근\s*(\d+)\s*(일|주|달|개월)", hint_l)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            if unit == "일": date_after = today - _td(days=n)
            elif unit == "주": date_after = today - _td(weeks=n)
            else: date_after = today - _td(days=n * 30)
        else:
            date_after = today - _td(days=7)

    allowed_exts = []
    for kw, exts in _EXT_KEYWORDS.items():
        if kw in hint_l:
            allowed_exts.extend(exts)

    noise = {"파일", "찾아줘", "가져와", "보내줘", "첨부", "오늘", "어제", "이번주", "지난주",
             "최근", "이번달", "지난달", "저번주", "저번달", "pdf", "이미지", "사진",
             "워드", "엑셀", "발표", "파워포인트", "텍스트", "코드", "압축"}
    import re as _re
    tokens = [t for t in _re.split(r"[\s,./]+", hint_l) if t and t not in noise and len(t) > 1]

    return date_after, list(set(allowed_exts)), tokens


def find_local_file_for_discord(hint: str) -> dict:
    """hint로 맥북 파일 검색 (이름/날짜/종류/키워드 혼합)"""
    from datetime import date as _date
    import time as _t

    date_after, allowed_exts, keywords = _parse_file_hint(hint)
    EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.idea',
                    'build', 'dist', '.next', '.claude', 'out', 'target', '.gradle'}

    candidates = []
    for root_dir in _SEND_FILE_SEARCH_ROOTS:
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
            for fname in files:
                if fname.startswith('.'):
                    continue
                full_path = os.path.join(root, fname)
                try:
                    stat = os.stat(full_path)
                except Exception:
                    continue
                size = stat.st_size
                if size > _MAX_SEND_FILE_SIZE:
                    continue

                fname_lower = fname.lower()
                _, ext = os.path.splitext(fname_lower)
                mtime = _date.fromtimestamp(stat.st_mtime)

                if date_after and mtime < date_after:
                    continue
                if allowed_exts and ext not in allowed_exts:
                    continue

                score = 0
                for kw in keywords:
                    if kw in fname_lower:
                        score += 10
                    if kw in full_path.lower():
                        score += 3
                if date_after:
                    score += 5
                if allowed_exts:
                    score += 3

                if score > 0 or (not keywords and (date_after or allowed_exts)):
                    mtime_str = datetime.fromtimestamp(stat.st_mtime).strftime("%m/%d %H:%M")
                    candidates.append({
                        "path": full_path,
                        "name": fname,
                        "size_kb": size // 1024,
                        "mtime_str": mtime_str,
                        "score": score,
                        "mtime_ts": stat.st_mtime,
                    })

    if not candidates:
        return {"error": f"❌ '{hint}' 조건에 맞는 파일을 찾지 못했어요.\n(검색: work-station / Downloads / Desktop / Documents)"}

    candidates.sort(key=lambda x: (-x["score"], -x["mtime_ts"]))
    top = candidates[:5]

    if len(top) == 1:
        c = top[0]
        return {"path": c["path"], "name": c["name"], "size_kb": c["size_kb"]}

    return {"candidates": [{"path": c["path"], "name": c["name"],
                            "size_kb": c["size_kb"], "mtime_str": c["mtime_str"]} for c in top]}


def read_pdf(path_or_hint: str, pages: str = "") -> dict:
    """PDF 파일 읽기. pages='1-3' 또는 '5' 형식으로 특정 페이지만 읽기 가능."""
    import re as _re

    if os.path.isabs(path_or_hint) and os.path.exists(path_or_hint):
        pdf_path = path_or_hint
    else:
        pdf_path, err = find_file(path_or_hint)
        if err:
            downloads = os.path.join(HOME, "Downloads")
            hint_lower = path_or_hint.lower()
            found = None
            for fname in os.listdir(downloads) if os.path.isdir(downloads) else []:
                if fname.lower().endswith(".pdf") and hint_lower in fname.lower():
                    found = os.path.join(downloads, fname)
                    break
            if found:
                pdf_path = found
            else:
                return {"error": err}
        if not pdf_path.lower().endswith(".pdf"):
            return {"error": f"❌ '{os.path.basename(pdf_path)}'은 PDF가 아니에요."}

    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)

            if pages:
                m = _re.match(r"(\d+)-(\d+)", pages)
                if m:
                    page_nums = list(range(int(m.group(1)) - 1, min(int(m.group(2)), total)))
                elif pages.isdigit():
                    page_nums = [int(pages) - 1]
                else:
                    page_nums = list(range(total))
            else:
                page_nums = list(range(total))

            lines = []
            for i in page_nums:
                if 0 <= i < total:
                    text = pdf.pages[i].extract_text() or ""
                    lines.append(f"── [ {i+1}페이지 / 전체 {total}p ] ──\n{text}")

            content = "\n\n".join(lines)
            name = os.path.basename(pdf_path)

            state.current_context = {
                "type": "pdf",
                "name": name,
                "path": pdf_path,
                "content": content,
                "total_pages": total,
                "loaded_pages": [p + 1 for p in page_nums],
            }
            return {
                "name": name,
                "content": content,
                "total_pages": total,
                "loaded_pages": [p + 1 for p in page_nums],
            }
    except Exception as e:
        return {"error": f"😥 PDF 읽기 오류: {e}"}


def read_local_file(target_hint):
    """파일 로드 + 내용 반환 (표시용)"""
    path, err = find_file(target_hint)
    if err:
        return err
    result, err = _read_file_at_path(path)
    if err:
        return err
    state.current_context = {
        "type": "file",
        "name": result["name"],
        "content": result["content"],
        "numbered_content": result["numbered_content"]
    }
    log_activity("파일 읽기", f"로드: {result['name']}")
    return result


def analyze_and_suggest_code(target_hint, question):
    if (state.current_context.get("type") == "file" and
            state.current_context.get("name", "").lower().replace(".py", "") in target_hint.lower()):
        content = state.current_context["content"]
        numbered = state.current_context["numbered_content"]
        name = state.current_context["name"]
        log_activity("코드 분석", f"캐시 사용: {name}")
    else:
        path, err = find_file(target_hint)
        if err:
            return err
        result, err = _read_file_at_path(path)
        if err:
            return err
        content = result["content"]
        numbered = result["numbered_content"]
        name = result["name"]
        state.current_context = {"type": "file", "name": name, "content": content, "numbered_content": numbered}
        log_activity("코드 분석", f"새 파일 로드: {name}")

    prompt = (
        f"파일명: '{name}'\n"
        f"줄번호 포함 전체 코드:\n```\n{numbered[:10000]}\n```\n\n"
        f"요청: {question}\n\n"
        "줄번호가 언급되면 반드시 해당 줄을 정확히 찾아 답해줘. "
        "코드 수정 제안 시 반드시 코드블록으로 감싸줘."
    )
    try:
        resp = state.ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "너는 시니어 개발자야. 줄번호 기반으로 정확히 코드를 분석해줘."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000
        )
        analysis = resp.choices[0].message.content
    except Exception as e:
        return f"😥 코드 분석 AI 오류: {e}"

    return {
        "type": "analysis",
        "name": name,
        "content": content,
        "numbered_content": numbered,
        "analysis": analysis
    }


def write_local_file(relative_path: str, content: str):
    """work-station 내에 파일 생성/수정. 경로 이탈 방지."""
    base = state.current_workspace["path"] if state.current_workspace else WORK_STATION_ROOT
    target = os.path.realpath(os.path.join(base, relative_path))
    if not target.startswith(os.path.realpath(base)):
        return f"❌ 허용된 경로 밖이에요: `{relative_path}`"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(target, WORK_STATION_ROOT)
        return f"✅ `{rel}` 파일 저장 완료! ({len(content.splitlines())}줄)"
    except Exception as e:
        return f"😥 파일 쓰기 오류: {e}"


def list_folder_contents(folder_hint):
    """폴더 내 파일 및 하위 폴더 목록 반환"""
    EXCLUDE = {'.git', 'node_modules', 'venv', '__pycache__', '.idea', 'build', 'dist', '.next', '.claude'}

    FOLDER_ALIAS = {
        "백엔드": "backend", "프론트": "frontend", "프론트엔드": "frontend",
        "리눅스": "linux", "자바": "java", "파이썬": "python",
    }

    hint_lower = folder_hint.strip().lower()
    for ko, en in FOLDER_ALIAS.items():
        hint_lower = hint_lower.replace(ko, en)

    search_root = state.current_workspace["path"] if state.current_workspace else WORK_STATION_ROOT

    # 현재 위치 or 루트 조회
    if hint_lower in ("현재", "여기", "지금", "this", "current", ""):
        target = search_root
    else:
        target = None
        for root, dirs, _ in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE and not d.startswith('.')]
            if hint_lower in os.path.basename(root).lower():
                target = root
                break
        if not target:
            # 정확히 매칭되는 것 못 찾으면 부분 매칭
            for root, dirs, _ in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in EXCLUDE and not d.startswith('.')]
                if any(hint_lower in d.lower() for d in dirs):
                    matched = next(d for d in dirs if hint_lower in d.lower())
                    target = os.path.join(root, matched)
                    break
        if not target:
            return f"❌ '{folder_hint}' 폴더를 찾지 못했어요."

    try:
        entries = []
        for entry in os.scandir(target):
            if entry.name.startswith('.') or entry.name in EXCLUDE:
                continue
            if entry.is_dir():
                entries.append(f"📁 {entry.name}/")
            else:
                size_kb = entry.stat().st_size // 1024
                entries.append(f"📄 {entry.name}  ({size_kb}KB)")

        if not entries:
            return f"📂 `{os.path.basename(target)}/` — 비어 있어요."

        rel = os.path.relpath(target, WORK_STATION_ROOT)
        header = f"📂 **`{rel}/`** ({len(entries)}개)\n\n"
        return header + "\n".join(sorted(entries))
    except Exception as e:
        return f"😥 폴더 목록 오류: {e}"
