#!/usr/bin/env python3
"""从已提交 Git 对象同步公开贴片快照，或打包本仓库快照。"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

SOURCE_REPOSITORY = "naizhao/Pilot-Kit-Box-ESP32-P4"
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
        content = replacements[name] if name in replacements else git(repository, "show", f"{ref}:{name}")
        for number, line in enumerate(content.decode("utf-8", errors="replace").splitlines(), 1):
            if home_path.search(line):
                findings.append(f"{name}:{number}: user-home-path")
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


def public_files(repository, ref, snapshot=False):
    sha = resolve_commit(repository, ref)
    prefix = "" if snapshot else "web/assembly/"
    files = {"index.html": read_blob(repository, sha, prefix + "index.html")}
    index_raw = read_blob(repository, sha, prefix + "data/index.json")
    index = json.loads(index_raw)
    if not isinstance(index, list) or not index:
        raise ValueError("公开索引必须是非空数组")
    ids = set()
    for entry in index:
        board_id = entry.get("id") if isinstance(entry, dict) else None
        if (not isinstance(board_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", board_id)
                or board_id in {"index", "batches", "internal"} or board_id in ids):
            raise ValueError(f"非法或重复公开板卡 id：{board_id!r}")
        ids.add(board_id)
        path = f"data/{board_id}.json"
        raw = read_blob(repository, sha, prefix + path)
        document = json.loads(raw)
        changed = False
        if isinstance(document, dict):
            for group in document.get("groups", []):
                if isinstance(group.get("note"), str):
                    original = group["note"]
                    cleaned = public_note(original)
                    if original != cleaned:
                        group["note"] = cleaned
                        changed = True
        files[path] = ((json.dumps(document, ensure_ascii=False, separators=(",", ":"))).encode()
                       if changed else raw)
    files["data/index.json"] = index_raw
    if snapshot:
        build = read_blob(repository, sha, "_build.json")
        metadata = json.loads(build)
        if (metadata.get("source_repository") != SOURCE_REPOSITORY
                or not re.fullmatch(r"[0-9a-f]{40}", metadata.get("source_sha", ""))):
            raise ValueError("快照缺少有效源仓库和 source_sha")
        files["_build.json"] = build
    else:
        files["_build.json"] = (json.dumps({"source_repository": SOURCE_REPOSITORY,
                                            "source_sha": sha}, indent=2) + "\n").encode()
    files["CNAME"] = b"smt.pilotkit.app\n"
    files[".nojekyll"] = b""
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
        target.write_bytes(content)


def export_site(repository, ref, output, snapshot=False):
    if output.exists():
        raise FileExistsError("打包目录必须不存在")
    if snapshot:
        check_public_privacy(repository, ref)
    files = public_files(repository, ref, snapshot)
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


def publish(repository, files, source_sha):
    check_publish_target(repository)
    check_public_privacy(repository, "HEAD", files)
    names = sorted(files)
    # 逐个显式路径；旧索引已不引用的 JSON 可留在历史中，但不进入站点产物。
    git(repository, "add", "--", *names)
    if not git(repository, "diff", "--cached", "--name-only").strip():
        print("快照无变化，跳过提交与推送。")
        return
    identity = os.environ.copy()
    identity.update({"GIT_AUTHOR_NAME": "Pilot Kit", "GIT_COMMITTER_NAME": "Pilot Kit",
                     "GIT_AUTHOR_EMAIL": "1012276+naizhao@users.noreply.github.com",
                     "GIT_COMMITTER_EMAIL": "1012276+naizhao@users.noreply.github.com"})
    git(repository, "commit", "-m", f"Update SMT snapshot from {source_sha}", env=identity)
    git(repository, "push", "origin", "HEAD:refs/heads/main")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot", action="store_true", help="从发布仓库快照打包，不读取源项目目录")
    parser.add_argument("--publish", action="store_true", help="明确授权将白名单快照提交并推送至发布仓库 main")
    args = parser.parse_args()
    if args.snapshot:
        if args.publish or args.output is None:
            parser.error("--snapshot 需要 --output，且不能与 --publish 合用")
        files = export_site(args.source, args.ref, args.output, snapshot=True)
    else:
        if args.output is not None:
            parser.error("同步固定写入脚本所在发布仓库，不接受 --output")
        if args.source.resolve() == ROOT.resolve():
            parser.error("源项目不能是发布仓库自身")
        if args.publish:
            check_publish_target(ROOT)
        files = public_files(args.source, args.ref)
        write_files(ROOT, files)
        if args.publish:
            publish(ROOT, files, json.loads(files["_build.json"])["source_sha"])
    print(f"已验证公开白名单：{len(files)} 文件；源 SHA：{json.loads(files['_build.json'])['source_sha']}")


if __name__ == "__main__":
    main()
