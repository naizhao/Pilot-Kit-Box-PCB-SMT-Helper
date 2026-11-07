#!/usr/bin/env python3
"""在本仓库准备公开贴片数据、发布或打包已提交站点。"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

PUBLISH_REPOSITORY = "naizhao/Pilot-Kit-Box-PCB-SMT-Helper"
ROOT = Path(__file__).resolve().parents[1]


def git(repository, *arguments, env=None):
    try:
        return subprocess.check_output(["git", "-C", str(repository), *arguments], stderr=subprocess.PIPE, env=env)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Git 操作失败（退出码 {error.returncode}）") from None


def resolve_commit(repository, ref):
    return git(repository, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def read_blob(repository, sha, path):
    mode = git(repository, "ls-tree", sha, "--", path).decode().split(" ", 1)[0]
    if mode != "100644":
        raise ValueError(f"只允许普通已提交文件：{path}")
    return git(repository, "show", f"{sha}:{path}")


def check_public_privacy(repository, ref, replacements=None):
    """检查将发布的提交与替换内容；诊断不回显个人目录。"""
    replacements = replacements or {}
    names = set(git(repository, "ls-tree", "-r", "--name-only", "-z", ref).decode().split("\0"))
    names.discard("")
    names.update(replacements)
    home_path = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+|[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>]+")
    findings = []
    for name in sorted(names):
        if name == "data/batches.json" or "internal" in Path(name).parts:
            raise ValueError("提交树含私有文件")
        content = replacements[name] if name in replacements else git(repository, "show", f"{ref}:{name}")
        for number, line in enumerate(content.decode("utf-8", errors="replace").splitlines(), 1):
            if home_path.search(line):
                findings.append(f"{name}:{number}: user-home-path")
        if name.endswith(".json"):
            reject_personal_notes(name, content)
    if findings:
        raise ValueError("公开内容包含个人目录：\n" + "\n".join(findings))


def public_note(note):
    """仅去除显式个人采购经历，保留型号纠错及装配技术提醒。"""
    original = note
    note = re.sub(r"§\d+ 清单已下单", "", note)
    note = re.sub(r"\*\*库存 \d+ 颗\*\*（每板 .*?）。",
                  lambda match: "相关料号：TS24CA。" if "TS24CA" in match.group() else "", note)
    note = note.replace("🔴**采购清单料号错配**：清单写 ", "料号核对：")
    note = note.replace("**料已到货，需万用表实测确认**", "装配前需万用表实测确认")
    note = re.sub(r"🔁\*\*本轮换料\*\*：\d{2}-\d{2} 那轮嘉立创配的是 (`[^`]+`)，本轮改用 (`[^`]+`)。",
                  r"选型确认：\1 → \2。", note)
    note = note.replace("重新上传 BOM 后要在匹配页确认换过来了。", "上传 BOM 后需在匹配页确认料号。")
    for phrase in ("自己贴则用现有元件本即可", "可用现有元件本", "用现有元件本", "用元件本"):
        note = note.replace(phrase, "")
    return note.strip(" ；;｜") if note != original else note


PUBLIC_CODE = {"index.html", "README.md", "LICENSE", "CNAME", ".nojekyll",
               ".gitignore", "data/.gitignore", "_build.json",
               "scripts/export_site.py", "tests/test_export_site.py", ".github/workflows/pages.yml"}


def validate_files(read, prepare=False):
    index_raw = read("data/index.json")
    index = json.loads(index_raw)
    if not isinstance(index, list) or not index:
        raise ValueError("公开索引必须是非空数组")
    files = {"data/index.json": index_raw}
    ids = set()
    for entry in index:
        board_id = entry.get("id") if isinstance(entry, dict) else None
        if (not isinstance(board_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", board_id)
                or board_id in {"index", "batches", "internal"} or board_id in ids):
            raise ValueError("非法或重复公开板卡 id")
        ids.add(board_id)
        name = f"data/{board_id}.json"
        raw = read(name)
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("板卡数据必须是对象")
        changed = False
        for group in document.get("groups", []):
            if isinstance(group.get("note"), str):
                cleaned = public_note(group["note"])
                if cleaned != group["note"]:
                    if not prepare:
                        raise ValueError(f"{name}: 原始个人 note，必须先 prepare")
                    group["note"] = cleaned
                    changed = True
        files[name] = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode() if changed else raw
    return files


def reject_personal_notes(name, raw):
    document = json.loads(raw)
    if isinstance(document, dict):
        for group in document.get("groups", []):
            if isinstance(group, dict) and isinstance(group.get("note"), str):
                if public_note(group["note"]) != group["note"]:
                    raise ValueError(f"{name}: 原始个人 note，必须先 prepare")


def check_contents(files):
    home = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+|[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>]+")
    for name, raw in files.items():
        if name.endswith(".json"):
            reject_personal_notes(name, raw)
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if home.search(line):
                raise ValueError(f"{name}:{number}: user-home-path")


def prepare(repository):
    def read(name):
        path = repository / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("拒绝符号链接公开文件")
        return path.read_bytes()
    files = validate_files(read, prepare=True)
    for name in PUBLIC_CODE:
        files[name] = read(name)
    check_contents(files)
    metadata = json.loads(files["_build.json"])
    if metadata != {"source_repository": PUBLISH_REPOSITORY}:
        raise ValueError("提交元数据必须仅标记本仓库来源")
    write_files(repository, files)
    return files


def public_files(repository, ref):
    sha = resolve_commit(repository, ref)
    read = lambda name: read_blob(repository, sha, name)
    files = validate_files(read)
    # 校验整个提交树，打包则仅限站点所需文件。
    check_public_privacy(repository, sha)
    if json.loads(read("_build.json")) != {"source_repository": PUBLISH_REPOSITORY}:
        raise ValueError("提交元数据必须仅标记本仓库来源")
    files["index.html"] = read("index.html")
    files["CNAME"] = read("CNAME")
    files[".nojekyll"] = read(".nojekyll")
    files["_build.json"] = (json.dumps({"source_repository": PUBLISH_REPOSITORY,
                                        "source_sha": sha}, indent=2) + "\n").encode()
    return files


def write_files(output, files):
    # 先完成全部读取和 JSON 校验，再开始写入白名单。
    if output.is_symlink():
        raise ValueError("拒绝符号链接输出目录")
    output = output.resolve()
    for name, content in files.items():
        target = output / name
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError(f"拒绝写入符号链接路径：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)


def export_site(repository, ref, output):
    if output.exists() or output.is_symlink():
        raise FileExistsError("打包目录必须不存在")
    files = public_files(repository, ref)
    write_files(output, files)
    return files


def check_publish_target(repository):
    origin = git(repository, "remote", "get-url", "--push", "origin").decode().strip()
    if origin not in {f"https://github.com/{PUBLISH_REPOSITORY}.git",
                      f"https://github.com/{PUBLISH_REPOSITORY}",
                      f"git@github.com:{PUBLISH_REPOSITORY}.git"}:
        raise ValueError("拒绝向非目标仓库推送")
    if git(repository, "branch", "--show-current").decode().strip() != "main":
        raise ValueError("发布必须位于新仓库 main 分支")
    if git(repository, "diff", "--cached", "--name-only").strip():
        raise ValueError("暂存区非空，拒绝卷入已有暂存内容")


def staged_files(repository):
    names = git(repository, "ls-files", "-z").decode().split("\0")
    files = {}
    for name in filter(None, names):
        mode = git(repository, "ls-files", "--stage", "--", name).decode().split(" ", 1)[0]
        if mode != "100644":
            raise ValueError("暂存树只允许普通文件")
        files[name] = git(repository, "show", f":{name}")
    allowed = PUBLIC_CODE | set(validate_files(files.__getitem__))
    if set(files) - allowed:
        raise ValueError("暂存树含非公开白名单文件")
    check_contents(files)
    if json.loads(files["_build.json"]) != {"source_repository": PUBLISH_REPOSITORY}:
        raise ValueError("暂存元数据必须仅标记本仓库来源")
    return files


def publish(repository):
    check_publish_target(repository)
    files = prepare(repository)
    # HEAD 中所有未被工作区替换的已提交文件也必须满足公开边界。
    names = set(filter(None, git(repository, "ls-tree", "-r", "--name-only", "-z", "HEAD").decode().split("\0")))
    if names - set(files):
        raise ValueError("提交树含非公开白名单文件")
    check_public_privacy(repository, "HEAD", files)
    git(repository, "add", "--", *sorted(files))
    staged_files(repository)
    if git(repository, "diff", "--cached", "--name-only").strip():
        identity = os.environ.copy()
        identity.update({"GIT_AUTHOR_NAME": "Pilot Kit", "GIT_COMMITTER_NAME": "Pilot Kit",
                         "GIT_AUTHOR_EMAIL": "1012276+naizhao@users.noreply.github.com",
                         "GIT_COMMITTER_EMAIL": "1012276+naizhao@users.noreply.github.com"})
        git(repository, "commit", "-m", "Update SMT assistant", env=identity)
    git(repository, "push", "origin", "HEAD:refs/heads/main")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--snapshot", action="store_true")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        if args.output is None:
            parser.error("--snapshot 需要 --output")
        files = export_site(ROOT, args.ref, args.output)
    else:
        if args.output is not None or args.ref != "HEAD":
            parser.error("只有 snapshot 接受 output/ref")
        files = publish(ROOT) if args.publish else prepare(ROOT)
    print("公开内容校验完成")


if __name__ == "__main__":
    main()
