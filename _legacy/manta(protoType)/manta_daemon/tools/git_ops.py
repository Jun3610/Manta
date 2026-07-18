"""
tools/git_ops.py — git 레포 탐색 및 상태 조회
"""
import os
import subprocess

from manta_daemon.config import WORK_STATION_ROOT
import manta_daemon.state as state


def _find_git_repos(root: str) -> list:
    """root 아래 .git 폴더를 가진 디렉토리 목록 (최근 수정 순)"""
    EXCLUDE = {'.git', 'node_modules', 'venv', '__pycache__', '.idea', 'build', 'dist', '.next', '.claude'}
    repos = []
    for entry in os.scandir(root):
        if entry.is_dir() and not entry.name.startswith('.') and entry.name not in EXCLUDE:
            git_dir = os.path.join(entry.path, ".git")
            if os.path.isdir(git_dir):
                repos.append((os.path.getmtime(git_dir), entry.path))
    repos.sort(reverse=True)
    return [p for _, p in repos]


def _git_status_from_root(git_root: str) -> str:
    """git_root 경로로 git 상태 문자열 반환"""
    try:
        status = subprocess.check_output(
            ["git", "status", "--short"], cwd=git_root, timeout=10
        ).decode().strip()
        log = subprocess.check_output(
            ["git", "log", "--oneline", "-5"], cwd=git_root, timeout=10
        ).decode().strip()
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=git_root, timeout=10
        ).decode().strip()
        summary_counts = {"M": 0, "A": 0, "D": 0, "?": 0}
        for line in status.splitlines():
            flag = line[:2].strip()[:1]
            if flag in summary_counts:
                summary_counts[flag] += 1
            else:
                summary_counts["M"] += 1
        status_parts = []
        if summary_counts["M"]: status_parts.append(f"수정 {summary_counts['M']}개")
        if summary_counts["A"]: status_parts.append(f"추가 {summary_counts['A']}개")
        if summary_counts["D"]: status_parts.append(f"삭제 {summary_counts['D']}개")
        if summary_counts["?"]: status_parts.append(f"미추적 {summary_counts['?']}개")
        status_summary = ", ".join(status_parts) if status_parts else "없음 (clean)"

        repo_name = os.path.basename(git_root)
        try:
            remote = subprocess.check_output(
                ["git", "remote", "get-url", "origin"], cwd=git_root, timeout=5
            ).decode().strip()
            remote_name = remote.rstrip("/").split("/")[-1].replace(".git", "")
        except Exception:
            remote_name = ""
        parts = [f"📁 **레포:** `{repo_name}`" + (f"  (`{remote_name}` on origin)" if remote_name and remote_name != repo_name else "")]
        parts.append(f"🌿 **브랜치:** `{branch}`")
        parts.append(f"**변경사항:** {status_summary}")
        if log:
            parts.append(f"**최근 커밋 (3개):**\n```\n{chr(10).join(log.splitlines()[:3])}\n```")
        return "\n".join(parts)
    except Exception as e:
        return f"😥 git 조회 오류: {e}"


def get_git_status_by_path(path: str) -> str:
    """절대 경로로 git 상태 조회"""
    git_root = path
    while True:
        if os.path.isdir(os.path.join(git_root, ".git")):
            break
        parent = os.path.dirname(git_root)
        if parent == git_root:
            return f"❌ `{path}` 안에 git 저장소가 없어요."
        git_root = parent
    return _git_status_from_root(git_root)


def get_git_status(folder_hint: str = ""):
    """현재 작업공간(또는 힌트 폴더)의 git 상태 조회"""
    base = state.current_workspace["path"] if state.current_workspace else None

    if folder_hint:
        search_root = base or WORK_STATION_ROOT
        for root, dirs, _ in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in {'.git', 'node_modules', 'venv', '__pycache__'}]
            if folder_hint.lower() in os.path.basename(root).lower():
                base = root
                break

    if not base:
        repos = _find_git_repos(WORK_STATION_ROOT)
        if not repos:
            return "❌ work-station 안에 git 저장소가 없어요."
        base = repos[0]

    git_root = base
    while True:
        if os.path.isdir(os.path.join(git_root, ".git")):
            break
        parent = os.path.dirname(git_root)
        if parent == git_root or not git_root.startswith(WORK_STATION_ROOT):
            return f"❌ `{base}` 안에 git 저장소가 없어요."
        git_root = parent

    return _git_status_from_root(git_root)
