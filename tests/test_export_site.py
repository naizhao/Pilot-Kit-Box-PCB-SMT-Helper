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

    def test_source_export_sanitizes_only_note_and_preserves_structure(self):
        files = self.files()
        data = {"groups": [{"note": "§7 清单已下单", "mpn": "TEST", "qty": 7}], "tracks": [1, 2]}
        files["web/assembly/data/v4.5-pwr.json"] = json.dumps(data).encode()
        output, _ = self.export(files)
        data["groups"][0]["note"] = ""
        self.assertEqual(json.loads((output / "data/v4.5-pwr.json").read_text()), data)

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

    def export(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "site"
        reads = []

        def read(repository, sha, path):
            reads.append((sha, path))
            return files[path]

        with patch.object(site, "read_blob", side_effect=read), patch.object(
            site, "resolve_commit", return_value="a" * 40
        ):
            site.export_site(Path("source"), "main", output)
        return output, reads

    def files(self, board="v4.5-pwr"):
        return {
            "web/assembly/index.html": b"<html>committed page</html>",
            "web/assembly/data/index.json": json.dumps([{"id": board}]).encode(),
            "web/assembly/data/" + board + ".json": b'{"id":"historical-id"}',
        }

    def test_exports_only_indexed_files_and_preserves_historical_document_id(self):
        files = self.files()
        files["web/assembly/data/batches.json"] = b'{"private":true}'
        files["web/assembly/internal/secrets.json"] = b"{}"
        output, reads = self.export(files)
        self.assertEqual(
            sorted(str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()),
            [".nojekyll", "CNAME", "_build.json", "data/index.json", "data/v4.5-pwr.json", "index.html"],
        )
        self.assertEqual((output / "index.html").read_bytes(), files["web/assembly/index.html"])
        self.assertEqual(json.loads((output / "_build.json").read_text())["source_sha"], "a" * 40)
        self.assertTrue(all(sha == "a" * 40 for sha, _ in reads))
        self.assertFalse(any("batches" in path or "internal" in path for _, path in reads))

    def test_rejects_unsafe_and_private_ids_before_reading_board(self):
        for board in ["../internal/secret", "batches", "internal", "x/y", "index", "x%2fy", "-bad", ""]:
            with self.subTest(board=board), self.assertRaises(ValueError):
                self.export(self.files(board))

    def test_validates_every_json_before_creating_output(self):
        files = self.files()
        files["web/assembly/data/v4.5-pwr.json"] = b"not json"
        with self.assertRaises(ValueError):
            self.export(files)

    def test_rejects_invalid_index_shapes_and_duplicates(self):
        for index in [{}, [], ["v3"], [{"id": "v4.5-pwr"}, {"id": "v4.5-pwr"}], [{"id": 3}]]:
            files = self.files()
            files["web/assembly/data/index.json"] = json.dumps(index).encode()
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.export(files)

    def test_never_reuses_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "private.json").write_text("private")
            with self.assertRaises(FileExistsError):
                site.export_site(Path("source"), "main", output)
            self.assertEqual((output / "private.json").read_text(), "private")

    def test_privacy_scan_covers_committed_readme_without_exposing_value(self):
        private = b"/Users/" + b"fictional-builder/projects/site"
        with patch.object(site, "git", side_effect=[b"README.md\0scripts/export_site.py\0", b"intro\n" + private, b"safe"]):
            with self.assertRaises(ValueError) as caught:
                site.check_public_privacy(Path("target"), "main")
        self.assertIn("README.md:2: user-home-path", str(caught.exception))
        self.assertNotIn(private.decode(), str(caught.exception))

    def test_privacy_scan_checks_proposed_files_and_windows_json_paths(self):
        values = [b"/home/" + b"fictional-builder/site", rb"C:\Users" + rb"\fictional-builder\site",
                  rb"C:\\Users" + rb"\\fictional-builder\\site"]
        for value in values:
            with self.subTest(value=value), patch.object(site, "git", return_value=b"README.md\0"):
                with self.assertRaises(ValueError) as caught:
                    site.check_public_privacy(Path("target"), "HEAD", {"README.md": value})
                self.assertIn("README.md:1: user-home-path", str(caught.exception))
                self.assertNotIn("fictional-builder", str(caught.exception))

    def test_privacy_scan_accepts_relative_commands_and_author_attribution(self):
        files = {"README.md": b"python3 scripts/export_site.py --source ../Pilot-Kit-Box-ESP32-P4\nCopyright Example Author"}
        with patch.object(site, "git", return_value=b"README.md\0"):
            site.check_public_privacy(Path("target"), "HEAD", files)

    def test_cli_accepts_relative_source(self):
        files = {"_build.json": json.dumps({"source_sha": "a" * 40}).encode()}
        with patch("sys.argv", ["export_site.py", "--source", "../Pilot-Kit-Box-ESP32-P4"]), patch.object(
            site, "public_files", return_value=files
        ) as read, patch.object(site, "write_files"), patch("builtins.print"):
            site.main()
        read.assert_called_once_with(Path("../Pilot-Kit-Box-ESP32-P4"), "HEAD")

    def test_snapshot_privacy_failure_precedes_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "site"
            with patch.object(site, "check_public_privacy", side_effect=ValueError("README.md:1: user-home-path")) as check:
                with self.assertRaises(ValueError):
                    site.export_site(Path("source"), "main", output, snapshot=True)
            check.assert_called_once_with(Path("source"), "main")
            self.assertFalse(output.exists())

    def test_publish_privacy_failure_precedes_any_git_write(self):
        with patch.object(site, "check_publish_target"), patch.object(site, "git") as command, patch.object(
            site, "check_public_privacy", side_effect=ValueError("README.md:1: user-home-path")
        ):
            with self.assertRaises(ValueError):
                site.publish(Path("target"), {"index.html": b"page"}, "a" * 40)
            command.assert_not_called()

    def test_publish_rejects_wrong_origin_and_existing_staging(self):
        for responses in [[b"git@github.com:naizhao/other.git"],
                          [b"git@github.com:naizhao/Pilot-Kit-Box-PCB-SMT-Helper.git", b"main", b"unrelated.txt"]]:
            with patch.object(site, "git", side_effect=responses) as command:
                with self.assertRaises(ValueError):
                    site.publish(Path("target"), {"index.html": b"page"}, "a" * 40)
                self.assertFalse(any(call.args[1] in {"add", "commit", "push"} for call in command.call_args_list))

    def test_publish_unchanged_snapshot_skips_commit_and_push(self):
        with patch.object(site, "check_publish_target"), patch.object(site, "check_public_privacy"), patch.object(site, "git", return_value=b"") as command:
            site.publish(Path("target"), {"index.html": b"page"}, "a" * 40)
            self.assertEqual([call.args[1] for call in command.call_args_list], ["add", "diff"])

    def test_publish_commit_overrides_inherited_personal_identity(self):
        with patch.dict(site.os.environ, {"GIT_AUTHOR_NAME": "Private", "GIT_AUTHOR_EMAIL": "private@example.invalid",
                                        "GIT_COMMITTER_NAME": "Private", "GIT_COMMITTER_EMAIL": "private@example.invalid"}), patch.object(
            site, "check_publish_target"
        ), patch.object(site, "check_public_privacy"), patch.object(
            site, "git", side_effect=[b"", b"index.html", b"", b""]
        ) as command:
            site.publish(Path("target"), {"index.html": b"page"}, "a" * 40)
        commit = [call for call in command.call_args_list if call.args[1] == "commit"][0]
        identity = commit.kwargs["env"]
        for role in ["AUTHOR", "COMMITTER"]:
            self.assertEqual(identity[f"GIT_{role}_NAME"], "Pilot Kit")
            self.assertEqual(identity[f"GIT_{role}_EMAIL"], "1012276+naizhao@users.noreply.github.com")

    def test_publish_stages_only_explicit_files_and_pushes_main_without_force(self):
        with patch.object(site, "check_publish_target"), patch.object(site, "check_public_privacy"), patch.object(
            site, "git", side_effect=[b"", b"index.html", b"", b""]
        ) as command:
            site.publish(Path("target"), {"index.html": b"page", "data/index.json": b"[]"}, "a" * 40)
            self.assertEqual(command.call_args_list[0].args[1:], ("add", "--", "data/index.json", "index.html"))
            self.assertEqual(command.call_args_list[-1].args[1:], ("push", "origin", "HEAD:refs/heads/main"))


if __name__ == "__main__":
    unittest.main()
