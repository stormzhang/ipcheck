import json
import tempfile
import unittest
from pathlib import Path

from ipcheck.local import (
    LocalChecker,
    extract_identity_fields,
    mask,
)


def make_checker(tmp, reveal=False):
    return LocalChecker(home=Path(tmp), reveal=reveal)


def result_by_label(rep, label):
    for r in rep.results:
        if r.label == label:
            return r
    return None


class MaskTests(unittest.TestCase):
    def test_short_value_keeps_first_two(self):
        self.assertEqual(mask("abc123"), "ab•••")
        self.assertEqual(mask("1234567890"), "12•••")

    def test_long_value_keeps_first5_last4(self):
        v = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(mask(v), "550e8…0000")

    def test_reveal_shows_plaintext(self):
        v = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(mask(v, reveal=True), v)

    def test_empty_value(self):
        self.assertEqual(mask(""), "")
        self.assertEqual(mask(None), "")


class ExtractIdentityFieldsTests(unittest.TestCase):
    def test_recursive_extraction(self):
        ev = {
            "event": {
                "account_uuid": "acc-1",
                "nested": [{"organization_uuid": "org-1"}, {"device_id": "dev-1"}],
                "emailAddress": "a@b.com",
            }
        }
        out = {}
        extract_identity_fields(ev, out)
        self.assertEqual(out, {
            "account_uuid": "acc-1",
            "org_uuid": "org-1",
            "device_id": "dev-1",
            "email": "a@b.com",
        })

    def test_first_value_wins(self):
        out = {}
        extract_identity_fields({"account_uuid": "first"}, out)
        extract_identity_fields({"account_uuid": "second"}, out)
        self.assertEqual(out["account_uuid"], "first")


class IdentityMergeTests(unittest.TestCase):
    def test_merge_by_account_uuid_unions_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(account_uuid="acc-1", email="a@b.com", source="滚动备份")
            c.add_identity(account_uuid="acc-1", org_uuid="org-1", source="遥测重发队列")
            self.assertEqual(len(c.identities), 1)
            ident = c.identities["acc-1"]
            self.assertEqual(ident["sources"], {"滚动备份", "遥测重发队列"})
            self.assertEqual(ident["email"], "a@b.com")
            self.assertEqual(ident["org_uuid"], "org-1")
            self.assertFalse(ident["is_current"])

    def test_key_falls_back_to_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(email="a@b.com", source="遥测重发队列")
            self.assertIn("a@b.com", c.identities)

    def test_is_current_is_sticky(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(account_uuid="acc-1", source="滚动备份")
            c.add_identity(account_uuid="acc-1", source=".claude.json(当前)", is_current=True)
            c.add_identity(account_uuid="acc-1", source="滚动备份")
            self.assertTrue(c.identities["acc-1"]["is_current"])

    def test_empty_identity_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(source="x")
            self.assertEqual(c.identities, {})


class IdentityReportTests(unittest.TestCase):
    def write_claude_json(self, tmp, email, uuid, org="org-c", user_id="dev-c"):
        Path(tmp, ".claude.json").write_text(json.dumps({
            "userID": user_id,
            "oauthAccount": {
                "emailAddress": email,
                "accountUuid": uuid,
                "organizationUuid": org,
            },
        }))

    def test_no_identity_is_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.check_claude_json()
            c.report_identities()
            r = result_by_label(c, "账号标识汇总")
            self.assertEqual(r.status, "pass")

    def test_current_only_is_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_claude_json(tmp, "cur@b.com", "acc-cur")
            c = make_checker(tmp)
            c.check_claude_json()
            c.report_identities()
            r = result_by_label(c, "账号标识汇总")
            self.assertEqual(r.status, "info")
            self.assertIn("无历史残留", r.summary)

    def test_historical_residue_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_claude_json(tmp, "cur@b.com", "acc-cur")
            bak_dir = Path(tmp, ".claude", "backups")
            bak_dir.mkdir(parents=True)
            bak_dir.joinpath(".claude.json.backup.1").write_text(json.dumps({
                "userID": "dev-old",
                "oauthAccount": {
                    "emailAddress": "old@b.com",
                    "accountUuid": "acc-old",
                    "organizationUuid": "org-old",
                },
            }))
            c = make_checker(tmp)
            c.check_claude_json()
            c.check_claude_dir()
            c.report_identities()
            r = result_by_label(c, "账号标识汇总")
            self.assertEqual(r.status, "fail")
            self.assertIn("1 个为历史残留", r.summary)
            # 滚动备份本身也报 fail
            self.assertEqual(result_by_label(c, "滚动备份").status, "fail")
            # 敏感值默认打码
            self.assertNotIn("old@b.com", "\n".join(r.details))

    def test_reveal_shows_plaintext_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_claude_json(tmp, "cur@b.com", "acc-cur")
            c = make_checker(tmp, reveal=True)
            c.check_claude_json()
            c.report_identities()
            r = result_by_label(c, "账号标识汇总")
            self.assertIn("cur@b.com", "\n".join(r.details))


class TelemetryQueueTests(unittest.TestCase):
    def test_telemetry_jsonl_feeds_identity_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tele = Path(tmp, ".claude", "telemetry")
            tele.mkdir(parents=True)
            tele.joinpath("1.json").write_text(
                json.dumps({"account_uuid": "acc-t", "device_id": "dev-t",
                            "email": "t@b.com"}) + "\nnot-json\n")
            c = make_checker(tmp)
            c.check_claude_dir()
            r = result_by_label(c, "遥测重发队列")
            self.assertEqual(r.status, "fail")
            self.assertIn("acc-t", c.identities)
            self.assertIn("dev-t", c.device_ids)
            self.assertFalse(c.device_ids["dev-t"]["is_current"])


class DeepNeedlesTests(unittest.TestCase):
    def test_needles_exclude_current_include_historical(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(email="cur@b.com", account_uuid="acc-cur",
                           org_uuid="org-cur", is_current=True)
            c.add_identity(email="old@b.com", account_uuid="acc-old",
                           org_uuid="org-old")
            c.add_device_id("dev-cur", is_current=True)
            c.add_device_id("dev-old")
            needles = c.deep_needles()
            self.assertEqual(needles, {"old@b.com", "acc-old", "org-old", "dev-old"})

    def test_deep_scan_finds_historical_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp, ".claude", "projects", "p1")
            proj.mkdir(parents=True)
            proj.joinpath("s1.jsonl").write_text('{"msg": "mail me at old@b.com"}\n')
            c = make_checker(tmp)
            c.add_identity(email="old@b.com", account_uuid="acc-old")
            c.check_deep_scan()
            r = result_by_label(c, "深度扫描")
            self.assertEqual(r.status, "warn")
            self.assertIn("历史标识文本", r.summary)

    def test_deep_scan_skips_without_historical_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.add_identity(email="cur@b.com", account_uuid="acc-cur", is_current=True)
            c.check_deep_scan()
            r = result_by_label(c, "深度扫描")
            self.assertEqual(r.status, "info")
            self.assertIn("跳过", r.summary)

    def test_deep_scan_clean_is_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp, ".claude", "projects", "p1")
            proj.mkdir(parents=True)
            proj.joinpath("s1.jsonl").write_text('{"msg": "nothing here"}\n')
            c = make_checker(tmp)
            c.add_identity(email="old@b.com", account_uuid="acc-old")
            c.check_deep_scan()
            r = result_by_label(c, "深度扫描")
            self.assertEqual(r.status, "pass")


class ShellConfigTests(unittest.TestCase):
    def test_hit_is_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".zshrc").write_text(
                'export ANTHROPIC_BASE_URL=https://example.com\nexport EDITOR=vim\n')
            c = make_checker(tmp)
            c.check_shell_configs()
            r = result_by_label(c, ".zshrc")
            self.assertEqual(r.status, "warn")
            self.assertEqual(result_by_label(c, "shell 配置"), None)

    def test_clean_is_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".zshrc").write_text("export EDITOR=vim\n")
            c = make_checker(tmp)
            c.check_shell_configs()
            self.assertEqual(result_by_label(c, "shell 配置").status, "pass")

    def test_no_files_is_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = make_checker(tmp)
            c.check_shell_configs()
            self.assertEqual(result_by_label(c, "shell 配置").status, "info")


if __name__ == "__main__":
    unittest.main()
