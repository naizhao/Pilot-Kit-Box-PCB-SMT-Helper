import contextlib
import importlib.util
import io
import subprocess
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_site.py"
spec = importlib.util.spec_from_file_location("export_site", SCRIPT)
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


class ExportSiteTests(unittest.TestCase):
    def test_public_note_removes_personal_records_but_preserves_technical_details(self):
        note = "**库存 9 颗**（每板 1 颗 → 够 9 块板）。50mA/12VDC；用现有元件本"
        self.assertEqual(site.public_note(note), "50mA/12VDC")
        note = "**库存 9 颗**（每板 1 颗 → 够 9 块板；与 TS24CA 并列全套最少，共同决定 9 块的上限）。24–960MHz 收发器"
        self.assertEqual(site.public_note(note), "相关料号：TS24CA。24–960MHz 收发器")
        self.assertEqual(site.public_note("Ih=100mA，16V 耐压，$0.1，库存 20 万"),
                         "Ih=100mA，16V 耐压，$0.1，库存 20 万")

    def test_public_note_neutralizes_purchase_history_without_losing_part_correction(self):
        note = "🔴**采购清单料号错配**：清单写 `C111`，那是22nH 的料号；正确应为 `C222`。**料已到货，需万用表实测确认**。 ⚠️SRF 2GHz"
        self.assertEqual(site.public_note(note),
                         "料号核对：`C111`，那是22nH 的料号；正确应为 `C222`。装配前需万用表实测确认。 ⚠️SRF 2GHz")
        note = "🔁**本轮换料**：08-29 那轮嘉立创配的是 `C111`，本轮改用 `C222`。重新上传 BOM 后要在匹配页确认换过来了。"
        self.assertEqual(site.public_note(note),
                         "选型确认：`C111` → `C222`。上传 BOM 后需在匹配页确认料号。")

    def test_git_failure_hides_command_and_stderr_paths(self):
        private = "/Users/" + "fictional-builder/private-project"
        failure = subprocess.CalledProcessError(128, ["git", "-C", private, "status"], stderr=private.encode())
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(site.subprocess, "check_output", side_effect=failure) as command, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(RuntimeError) as caught:
                site.git(Path(private), "status")
        self.assertEqual(str(caught.exception), "Git 操作失败（退出码 128）")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn(private, stdout.getvalue() + stderr.getvalue() + str(caught.exception))
        self.assertEqual(command.call_args.kwargs["stderr"], subprocess.PIPE)

    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "data").mkdir()
        for name in site.PUBLIC_CODE:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"safe")
        (root / "_build.json").write_text(json.dumps({"source_repository": site.PUBLISH_REPOSITORY}))
        (root / "data/index.json").write_text('[{"id":"v3"}]')
        (root / "data/v3.json").write_text('{"id":"historical-id","groups":[{"mpn":"TEST","note":"§7 清单已下单"}]}')
        return root

    def test_prepare_direct_workspace_without_parent_or_git_and_never_reads_batches(self):
        root = self.workspace()
        (root / "data/batches.json").write_bytes(b"not json private")
        with patch.object(site, "git", side_effect=AssertionError("unexpected Git read")):
            files = site.prepare(root)
        board = json.loads(files["data/v3.json"])
        self.assertEqual(board["id"], "historical-id")
        self.assertEqual(board["groups"], [{"mpn": "TEST", "note": ""}])
        self.assertEqual((root / "data/batches.json").read_bytes(), b"not json private")
        self.assertNotIn("data/batches.json", files)

    def test_prepare_validates_all_before_any_write(self):
        root = self.workspace()
        original = (root / "data/v3.json").read_bytes()
        (root / "data/index.json").write_text('[{"id":"v3"},{"id":"v4"}]')
        (root / "data/v4.json").write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            site.prepare(root)
        self.assertEqual((root / "data/v3.json").read_bytes(), original)

    def test_invalid_indexes(self):
        for index in [[], {}, ["v3"], [{"id":"batches"}], [{"id":"../x"}], [{"id":"v3"},{"id":"v3"}]]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                site.validate_files(lambda name: json.dumps(index).encode())

    def test_snapshot_raw_private_note_fails_before_output_exists(self):
        root = self.workspace()
        output = root / "output"
        with patch.object(site, "resolve_commit", return_value="a" * 40), patch.object(
            site, "read_blob", side_effect=lambda repo, sha, name: (root / name).read_bytes()
        ), self.assertRaises(ValueError):
            site.export_site(root, "HEAD", output)
        self.assertFalse(output.exists())

    def test_standalone_snapshot_uses_own_ref_and_whitelist(self):
        root = self.workspace()
        site.prepare(root)
        (root / "data/batches.json").write_bytes(b"private")
        output = root / "output"
        with patch.object(site, "resolve_commit", return_value="a" * 40), patch.object(
            site, "read_blob", side_effect=lambda repo, sha, name: (root / name).read_bytes()
        ), patch.object(site, "check_public_privacy"):
            files = site.export_site(root, "HEAD", output)
        self.assertEqual(set(files), {"index.html", "data/index.json", "data/v3.json", "CNAME", ".nojekyll", "_build.json"})
        self.assertEqual(json.loads(files["_build.json"]), {"source_repository": site.PUBLISH_REPOSITORY, "source_sha": "a" * 40})
        self.assertFalse((output / "data/batches.json").exists())
        with self.assertRaises(FileExistsError):
            site.export_site(root, "HEAD", output)

    def test_privacy_scans_documentation_and_windows_paths_without_exposing_value(self):
        for value in [b"/Users/" + b"fictional-builder/site", b"/home/" + b"fictional-builder/site",
                      rb"C:\Users" + rb"\fictional-builder\site"]:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                site.check_contents({"README.md": value})
            self.assertIn("README.md:1: user-home-path", str(caught.exception))
            self.assertNotIn("fictional-builder", str(caught.exception))

    def test_staged_tree_reads_actual_index_and_rejects_private_file(self):
        root = self.workspace()
        files = site.prepare(root)
        files["data/batches.json"] = b"private"
        def fake(repo, *args, **kwargs):
            if args == ("ls-files", "-z"):
                return "\0".join(files).encode()
            if args[0] == "ls-files":
                return b"100644 fake 0\tname"
            return files[args[1][1:]]
        with patch.object(site, "git", side_effect=fake), self.assertRaises(ValueError):
            site.staged_files(root)
        del files["data/batches.json"]
        files["README.md"] = b"/home/" + b"fictional-builder/private"
        with patch.object(site, "git", side_effect=fake), self.assertRaises(ValueError):
            site.staged_files(root)

    def test_publish_rejects_wrong_origin_and_existing_staging_before_writes(self):
        for responses in [[b"git@github.com:naizhao/other.git"],
                          [b"git@github.com:naizhao/Pilot-Kit-Box-PCB-SMT-Helper.git", b"main", b"README.md"]]:
            with patch.object(site, "git", side_effect=responses) as command, self.assertRaises(ValueError):
                site.publish(Path("target"))
            self.assertFalse(any(call.args[1] in {"add", "commit", "push"} for call in command.call_args_list))

    def test_publish_unchanged_pushes_again_and_changed_uses_project_identity(self):
        for changed in [False, True]:
            with patch.object(site, "check_publish_target"), patch.object(site, "prepare", return_value={"index.html": b"page"}), patch.object(
                site, "check_public_privacy"
            ), patch.object(site, "staged_files"), patch.object(site, "git", side_effect=lambda repo, *args, **kw: b"index.html" if args[0] == "diff" and changed else b"") as command:
                site.publish(Path("target"))
            calls = command.call_args_list
            self.assertIn(("add", "--", "index.html"), [call.args[1:] for call in calls])
            self.assertEqual(calls[-1].args[1:], ("push", "origin", "HEAD:refs/heads/main"))
            commits = [call for call in calls if call.args[1] == "commit"]
            self.assertEqual(len(commits), int(changed))
            if changed:
                for role in ["AUTHOR", "COMMITTER"]:
                    self.assertEqual(commits[0].kwargs["env"][f"GIT_{role}_NAME"], "Pilot Kit")
                    self.assertEqual(commits[0].kwargs["env"][f"GIT_{role}_EMAIL"], "1012276+naizhao@users.noreply.github.com")

    def test_committed_tree_checks_documentation_and_raw_notes(self):
        cases = [("README.md", b"/home/" + b"fictional-builder/private"),
                 ("data/v3.json", '{"groups":[{"note":"§7 清单已下单"}]}'.encode()),
                 ("data/batches.json", b"{}"), ("internal/notes.txt", b"private")]
        for name, raw in cases:
            with self.subTest(name=name), patch.object(site, "git", side_effect=[(name + "\0").encode(), raw]), self.assertRaises(ValueError):
                site.check_public_privacy(Path("repo"), "HEAD")

    def test_publish_failed_actual_index_check_prevents_commit_and_push(self):
        with patch.object(site, "check_publish_target"), patch.object(site, "prepare", return_value={"index.html": b"page"}), patch.object(
            site, "check_public_privacy"
        ), patch.object(site, "staged_files", side_effect=ValueError("private index blob")), patch.object(site, "git", return_value=b"") as command, self.assertRaises(ValueError):
            site.publish(Path("target"))
        self.assertFalse(any(call.args[1] in {"commit", "push"} for call in command.call_args_list))

    def test_batches_is_ignored(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(subprocess.check_output(["git", "-C", str(root), "check-ignore", "data/batches.json"]).decode().strip(), "data/batches.json")


if __name__ == "__main__":
    unittest.main()
