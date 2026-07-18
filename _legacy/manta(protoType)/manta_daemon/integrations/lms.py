"""
integrations/lms.py — LMS 로그인, 강의/과제/자료 조회
"""
import re
from bs4 import BeautifulSoup

from manta_daemon.config import LMS_ID, LMS_PW, LMS_BASE
import manta_daemon.state as state
from manta_daemon.utils.helpers import log_activity


def _lms_login():
    """LMS에 로그인. 성공 시 True, 실패 시 에러 메시지 반환"""
    if not LMS_ID or not LMS_PW:
        return "❌ .env에 LMS_ID, LMS_PW가 설정되지 않았어요."
    try:
        r = state.lms_session.get(f"{LMS_BASE}/ilos/main/member/login_form.acl", timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        challenge = soup.find("input", {"name": "challenge"})
        challenge_val = challenge["value"] if challenge and challenge.get("value") else ""

        payload = {
            "returnURL": "",
            "challenge": challenge_val,
            "response": "",
            "usr_id": LMS_ID,
            "usr_pwd": LMS_PW,
        }
        res = state.lms_session.post(f"{LMS_BASE}/ilos/lo/login.acl", data=payload, timeout=10)
        final_soup = BeautifulSoup(res.text, "html.parser")
        js_redirect = "main_form.acl" in res.text or "main/main" in res.text
        logout_link = final_soup.find("a", href=lambda h: h and "logout" in h.lower())
        my_info = final_soup.find("a", href=lambda h: h and "myinfo" in (h or "").lower())
        if js_redirect or logout_link or my_info or "logout" in res.text.lower():
            state.lms_logged_in = True
            log_activity("LMS", f"로그인 성공: {LMS_ID}")
            return True
        err_div = final_soup.find("div", class_=re.compile(r"error|alert|msg", re.I))
        err_msg = err_div.get_text(strip=True)[:100] if err_div else "로그인 실패 (아이디/비밀번호 확인)"
        return f"❌ LMS 로그인 실패: {err_msg}"
    except Exception as e:
        return f"😥 LMS 로그인 오류: {e}"


def _lms_ensure_login():
    """로그인 상태 확인 후 필요 시 재로그인"""
    if state.lms_logged_in:
        try:
            r = state.lms_session.get(f"{LMS_BASE}/ilos/st/main/main_form.acl", timeout=8)
            if "logout" in r.text.lower() or "로그아웃" in r.text:
                return True
        except Exception:
            pass
        state.lms_logged_in = False
    return _lms_login()


def lms_get_courses():
    """수강 중인 강의 목록 반환"""
    result = _lms_ensure_login()
    if result is not True:
        return None, result
    try:
        r = state.lms_session.post(f"{LMS_BASE}/ilos/mp/course_register_list.acl",
                             data={"SCH_DT": ""}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        courses = []
        seen = set()

        for em in soup.find_all("em", attrs={"kj": True}):
            kjkey = em.get("kj", "").strip()
            title = em.get("title", "")
            name = re.sub(r"\s*강의실.*", "", title).strip()
            if kjkey and name and kjkey not in seen:
                seen.add(kjkey)
                courses.append({"name": name, "kjkey": kjkey})

        if not courses:
            for m in re.finditer(r"eclassRoom\('([^']+)'\)[^>]*title=\"([^\"]+)\"", r.text):
                kjkey, title = m.group(1), m.group(2)
                name = re.sub(r"\s*강의실.*", "", title).strip()
                if kjkey and name and kjkey not in seen:
                    seen.add(kjkey)
                    courses.append({"name": name, "kjkey": kjkey})

        if not courses:
            return [], "📭 수강 중인 강의를 찾지 못했어요."
        return courses, None
    except Exception as e:
        return None, f"😥 강의 목록 조회 오류: {e}"


def lms_get_notices(kjkey):
    """강의 공지사항 조회"""
    try:
        url = f"{LMS_BASE}/ilos/st/course/notice_list_form.acl?KJKEY={kjkey}&encoding=utf-8"
        r = state.lms_session.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for row in soup.select("table tr, ul.board-list li, div.list-item"):
            title_el = row.find("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            date_el = row.find(class_=re.compile(r"date|time", re.I)) or row.find("td", string=re.compile(r"\d{4}"))
            date = date_el.get_text(strip=True) if date_el else ""
            if title and len(title) > 1:
                items.append(f"• {title}" + (f"  [{date}]" if date else ""))
        if not items:
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            lines = [l for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]
            items = lines[:30]
        return "\n".join(items[:20]) if items else "📭 공지사항이 없어요."
    except Exception as e:
        return f"😥 공지 조회 오류: {e}"


def _lms_enter_course(kjkey: str) -> bool:
    """eclass_room2로 강의실 세션 진입. 성공 시 True 반환."""
    try:
        r = state.lms_session.post(
            f"{LMS_BASE}/ilos/st/course/eclass_room2.acl",
            data={"KJKEY": kjkey, "returnData": "json",
                  "returnURI": "/ilos/st/course/submain_form.acl", "encoding": "utf-8"},
            timeout=10
        )
        d = r.json()
        if d.get("isError"):
            return False
        state.lms_session.get(f"{LMS_BASE}/ilos/st/course/submain_form.acl", timeout=10)
        return True
    except Exception:
        return False


def lms_get_homework(kjkey: str, course_name: str = "") -> str:
    """강의 과제 목록 조회 (제출 상태 포함) — submain_form 파싱"""
    try:
        state.lms_session.post(f"{LMS_BASE}/ilos/mp/course_register_list.acl", data={"SCH_DT": ""}, timeout=10)
        if not _lms_enter_course(kjkey):
            return "⚠️ 강의실 진입에 실패했어요. LMS에 직접 접속해서 확인해줘요."

        r = state.lms_session.get(f"{LMS_BASE}/ilos/st/course/submain_form.acl", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        assignments = []
        for el in soup.find_all(onclick=re.compile(r"report_view_form")):
            oc = el.get("onclick", "")
            seq_m = re.search(r"RT_SEQ=(\d+)", oc)
            if not seq_m:
                continue
            raw = el.get_text("\n", strip=False)
            if "종료" in raw:
                status = "종료"
            elif "진행중" in raw:
                status = "진행중"
            else:
                status = "?"
            lines = [l.strip().lstrip("\xa0").strip() for l in raw.splitlines() if l.strip().lstrip("\xa0").strip()]
            title = next((l for l in lines if l and l not in ["과제", "종료", "진행중", "?"]), "")
            assignments.append({"title": title, "status": status, "seq": seq_m.group(1)})

        if not assignments:
            return f"📭 {course_name or ''} 과제가 없어요."

        submitted = [a for a in assignments if a["status"] == "종료"]
        ongoing = [a for a in assignments if a["status"] == "진행중"]

        lines = []
        course_label = f"**{course_name}** " if course_name else ""
        lines.append(f"📋 {course_label}과제 목록 (총 {len(assignments)}개)")
        if ongoing:
            lines.append(f"\n🔴 **진행중 (미제출 포함)** — {len(ongoing)}개")
            for a in ongoing:
                lines.append(f"  • {a['title']}")
        if submitted:
            lines.append(f"\n✅ **종료된 과제** — {len(submitted)}개")
            for a in submitted:
                lines.append(f"  • {a['title']}")
        return "\n".join(lines)
    except Exception as e:
        return f"😥 과제 조회 오류: {e}"


def lms_get_materials(kjkey):
    """강의 자료 목록 조회"""
    try:
        url = f"{LMS_BASE}/ilos/st/course/lecture_material_list_form.acl?KJKEY={kjkey}&encoding=utf-8"
        r = state.lms_session.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(k in href for k in ["material", "file", "down", "lecture"]):
                name = a.get_text(strip=True)
                if name and len(name) > 2 and name not in ["다운로드", "download", "파일"]:
                    items.append(f"• {name}")
        if not items:
            for row in soup.select("table tr"):
                tds = row.find_all("td")
                if tds:
                    text = " | ".join(td.get_text(strip=True) for td in tds if td.get_text(strip=True))
                    if text and len(text) > 3:
                        items.append(f"• {text}")
        if not items:
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            lines = [l for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]
            items = lines[:30]
        return "\n".join(items[:20]) if items else "📭 강의 자료가 없어요."
    except Exception as e:
        return f"😥 강의 자료 조회 오류: {e}"


def lms_get_course_home(kjkey):
    """강의 홈 전체 현황 (주차별 학습, 출석 등)"""
    try:
        url = f"{LMS_BASE}/ilos/st/course/course_home_form.acl?KJKEY={kjkey}&encoding=utf-8"
        r = state.lms_session.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
            tag.decompose()
        lines = [l for l in soup.get_text("\n", strip=True).splitlines() if l.strip() and len(l.strip()) > 1]
        return "\n".join(lines[:60]) if lines else "📭 강의 홈 정보를 가져오지 못했어요."
    except Exception as e:
        return f"😥 강의 홈 조회 오류: {e}"


def lms_get_todo_list() -> str:
    """Todo List (미완료 항목 전체) — 과제·강의·퀴즈 포함"""
    result = _lms_ensure_login()
    if result is not True:
        return result
    try:
        state.lms_session.post(f"{LMS_BASE}/ilos/mp/course_register_list.acl", data={"SCH_DT": ""}, timeout=10)
        r = state.lms_session.post(
            f"{LMS_BASE}/ilos/mp/todo_list.acl",
            data={"todoKjList": "", "chk_cate": "ALL", "encoding": "utf-8"},
            timeout=10
        )
        if r.status_code != 200 or len(r.text) < 100:
            return "📭 Todo 목록을 가져오지 못했어요."

        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for wrap in soup.find_all(class_="todo_wrap"):
            if "no_data" in wrap.get("class", []):
                continue
            gubun_input = wrap.find("input", {"id": re.compile(r"^gubun_")})
            title_el = wrap.find(class_="todo_title")
            subjt_el = wrap.find(class_="todo_subjt")
            d_day_el = wrap.find(class_="todo_d_day")
            date_els = wrap.find_all(class_="todo_date")

            gubun = gubun_input["value"] if gubun_input else ""
            title = title_el.get_text(strip=True) if title_el else ""
            course = subjt_el.get_text(strip=True) if subjt_el else ""
            d_day = d_day_el.get_text(strip=True) if d_day_el else ""
            deadline = date_els[-1].get_text(strip=True) if date_els else ""

            type_label = {"report": "📝과제", "lecture_weeks": "🎬강의", "test": "📝퀴즈",
                          "project": "🗂팀프로젝트", "survey": "📋설문", "discuss": "💬토론"}.get(gubun, f"[{gubun}]")
            items.append(f"{type_label} **{title}** | {course} | {d_day} `{deadline}`")

        if not items:
            return "🎉 미완료 할 일이 없어요! Todo 리스트가 비어있어요."
        return f"📌 **LMS Todo 목록** (총 {len(items)}개)\n\n" + "\n".join(items)
    except Exception as e:
        return f"😥 Todo 조회 오류: {e}"


def lms_get_all_homework():
    """모든 수강 강의의 미완료 과제 요약 (Todo 기반)"""
    result = _lms_ensure_login()
    if result is not True:
        return result
    try:
        state.lms_session.post(f"{LMS_BASE}/ilos/mp/course_register_list.acl", data={"SCH_DT": ""}, timeout=10)
        r = state.lms_session.post(
            f"{LMS_BASE}/ilos/mp/todo_list.acl",
            data={"todoKjList": "", "chk_cate": "ALL", "encoding": "utf-8"},
            timeout=10
        )
        soup = BeautifulSoup(r.text, "html.parser")

        by_course: dict = {}
        for wrap in soup.find_all(class_="todo_wrap"):
            if "no_data" in wrap.get("class", []):
                continue
            gubun_input = wrap.find("input", {"id": re.compile(r"^gubun_")})
            title_el = wrap.find(class_="todo_title")
            subjt_el = wrap.find(class_="todo_subjt")
            d_day_el = wrap.find(class_="todo_d_day")
            date_els = wrap.find_all(class_="todo_date")

            gubun = gubun_input["value"] if gubun_input else ""
            title = title_el.get_text(strip=True) if title_el else ""
            course = subjt_el.get_text(strip=True) if subjt_el else "기타"
            d_day = d_day_el.get_text(strip=True) if d_day_el else ""
            deadline = date_els[-1].get_text(strip=True) if date_els else ""

            if not title:
                continue
            type_label = {"report": "📝과제", "lecture_weeks": "🎬강의", "test": "📝퀴즈",
                          "project": "🗂팀프로젝트", "survey": "📋설문", "discuss": "💬토론"}.get(gubun, "📌기타")
            entry = f"  • {type_label} {title}  `{deadline}` {d_day}"
            by_course.setdefault(course, []).append(entry)

        if not by_course:
            return "🎉 현재 미완료 과제가 없어요! 모두 제출 완료!"

        lines = [f"📌 **LMS 미완료 목록** (총 {sum(len(v) for v in by_course.values())}개)\n"]
        for course, entries in by_course.items():
            lines.append(f"**📚 {course}**")
            lines.extend(entries)
        return "\n".join(lines)
    except Exception as e:
        return f"😥 과제 목록 조회 오류: {e}"
