import json
import tempfile
import unittest
from pathlib import Path
from reference_core.integrity import canonical_bytes, safe_path, seal_bundle, verify_bundle


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "evidence").mkdir()
        (self.root / "evidence" / "V1.bin").write_bytes(b"synthetic unit-test evidence")
        (self.root / "source.bin").write_bytes(b"synthetic unit-test source; not video")
        self.payloads = [{"kind": "event", "event_id": "E1", "evidence_paths": ["evidence/V1.bin"]}]
        self.head = seal_bundle(self.root, self.payloads)

    def test_anchored_bundle(self):
        result = verify_bundle(self.root, self.head)
        self.assertEqual(result["integrity"], "valid")
        self.assertTrue(result["anchored"])

    def test_unanchored_bundle(self):
        result = verify_bundle(self.root)
        self.assertEqual(result["integrity"], "valid")
        self.assertFalse(result["anchored"])

    def test_changed_evidence(self):
        (self.root / "evidence" / "V1.bin").write_bytes(b"changed")
        self.assertEqual(verify_bundle(self.root, self.head)["integrity"], "failed")

    def test_changed_source(self):
        (self.root / "source.bin").write_bytes(b"changed")
        self.assertEqual(verify_bundle(self.root, self.head)["integrity"], "failed")

    def test_missing_file(self):
        (self.root / "source.bin").unlink()
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")

    def test_extra_file(self):
        (self.root / "extra.bin").write_bytes(b"unexpected")
        self.assertIn("FILE_INVENTORY_MISMATCH", verify_bundle(self.root)["errors"])

    def test_wrong_anchor(self):
        self.assertIn("TRUST_ANCHOR_MISMATCH", verify_bundle(self.root, "f"*64)["errors"])

    def test_chain_order(self):
        path = self.root / "commits.json"
        commits = json.loads(path.read_bytes())
        path.write_bytes(canonical_bytes(list(reversed(commits))))
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")

    def test_head_whitespace_change(self):
        (self.root / "HEAD").write_text(self.head + " \n")
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")

    def test_control_whitespace_change(self):
        path = self.root / "manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")

    def test_manifest_tampering(self):
        path = self.root / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["files"]["source.bin"] = "f"*64
        path.write_bytes(canonical_bytes(manifest))
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")

    def test_reseal_without_anchor_is_not_authentication(self):
        for filename in ["manifest.json", "commits.json", "HEAD"]:
            (self.root / filename).unlink()
        (self.root / "source.bin").write_bytes(b"coherently replaced source")
        new_head = seal_bundle(self.root, self.payloads)
        self.assertNotEqual(new_head, self.head)
        self.assertEqual(verify_bundle(self.root)["integrity"], "valid")
        self.assertEqual(verify_bundle(self.root, self.head)["integrity"], "failed")

    def test_overwrite_refused(self):
        with self.assertRaises(ValueError):
            seal_bundle(self.root, [])

    def test_canonical_key_order(self):
        self.assertEqual(canonical_bytes({"b": 1, "a": 2}), canonical_bytes({"a": 2, "b": 1}))

    def test_float_refused(self):
        with self.assertRaises(ValueError):
            canonical_bytes({"score": 0.5})

    def test_parent_traversal(self):
        with self.assertRaises(ValueError):
            safe_path(self.root, "../outside")

    def test_absolute_path(self):
        with self.assertRaises(ValueError):
            safe_path(self.root, "/tmp/outside")

    def test_symlink(self):
        (self.root / "link").symlink_to(self.root / "source.bin")
        self.assertEqual(verify_bundle(self.root)["integrity"], "failed")
