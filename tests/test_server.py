import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from server import (  # noqa: E402
    AlgorithmServer,
    build_config,
    clean_relative_path,
    collision_name,
    parse_password_hash,
    verify_password,
)


class ServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"
        self.root.mkdir()
        (self.root / "algo10").mkdir()
        (self.root / "algo2").mkdir()
        (self.root / "algo2" / "hello.txt").write_text("hello 世界", encoding="utf-8")
        (self.root / "plain.bin").write_bytes(b"0123456789")
        self.docx_path = self.root / "sample.docx"
        with zipfile.ZipFile(self.docx_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'><Default Extension='xml' ContentType='application/xml'/></Types>",
            )
            archive.writestr(
                "word/document.xml",
                "<?xml version='1.0'?><w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>DOCX preview test</w:t></w:r></w:p></w:body></w:document>",
            )
        self.outside = Path(self.temporary.name) / "outside.txt"
        self.outside.write_text("outside", encoding="utf-8")
        os.symlink(self.outside, self.root / "outside-link")

        self.operation_log = Path(self.temporary.name) / "operations.log"
        config = build_config(
            self.root,
            username="bupt",
            password="jsjskt1",
            max_upload_bytes=1024,
            min_free_bytes=0,
            preview_bytes=6,
            docx_preview_bytes=2048,
            docx_unpacked_bytes=4096,
            docx_max_entries=100,
            operation_log=self.operation_log,
            allowed_hosts="127.0.0.1",
            request_timeout_seconds=15,
        )
        self.server = AlgorithmServer(("127.0.0.1", 0), config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.cookie = ""
        self.csrf = ""

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        request_headers = {"Accept": "application/json"}
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        if headers:
            request_headers.update(headers)
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        self.connection.request(method, path, body=body, headers=request_headers)
        response = self.connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        payload = json.loads(raw.decode("utf-8")) if raw and "application/json" in content_type else raw
        return response.status, payload, dict(response.getheaders())

    def login(self):
        status, payload, headers = self.request(
            "POST", "/api/login", {"username": "bupt", "password": "jsjskt1"}
        )
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = payload["csrf"]
        return payload

    def csrf_headers(self):
        return {"X-CSRF-Token": self.csrf}

    def test_static_assets_and_authentication_boundary(self):
        for path, expected_type in [
            ("/", "text/html"),
            ("/static/app.css", "text/css"),
            ("/static/app.js", "application/javascript"),
            ("/static/vendor/jszip-3.10.1/dist/jszip.min.js", "application/javascript"),
            ("/static/vendor/docx-preview-0.4.0/dist/docx-preview.min.js", "application/javascript"),
        ]:
            status, body, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected_type, headers["Content-Type"])
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")

        status, payload, _ = self.request("GET", "/api/list?path=")
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "authentication_required")

        status, payload, _ = self.request(
            "POST", "/api/login", {"username": "错误用户", "password": "wrong"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "invalid_credentials")

        session = self.login()
        self.assertEqual(session["username"], "bupt")
        status, payload, _ = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(payload["username"], "bupt")

    def test_docx_preflight_validation(self):
        query = urllib.parse.urlencode({"path": "sample.docx"})
        status, payload, _ = self.request("GET", f"/api/docx-info?{query}")
        self.assertEqual(status, 401)
        status, payload, _ = self.request("GET", f"/api/docx-file?{query}")
        self.assertEqual(status, 401)

        self.login()
        status, payload, _ = self.request("GET", f"/api/docx-info?{query}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], "sample.docx")
        self.assertEqual(payload["entries"], 2)
        self.assertGreater(payload["unpacked_size"], 0)

        expected_docx = self.docx_path.read_bytes()
        status, body, headers = self.request("GET", f"/api/docx-file?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(body, expected_docx)
        self.assertEqual(int(headers["Content-Length"]), len(expected_docx))
        self.assertEqual(
            headers["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        status, body, headers = self.request("HEAD", f"/api/docx-file?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(expected_docx))

        # Preview reads the current file on every request instead of serving a stale conversion.
        with zipfile.ZipFile(self.docx_path, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/latest-version.xml", "latest DOCX content")
        updated_docx = self.docx_path.read_bytes()
        status, body, headers = self.request("GET", f"/api/docx-file?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(body, updated_docx)
        self.assertNotEqual(body, expected_docx)
        self.assertEqual(int(headers["Content-Length"]), len(updated_docx))

        (self.root / "broken.docx").write_bytes(b"not a docx")
        broken_query = urllib.parse.urlencode({"path": "broken.docx"})
        status, payload, _ = self.request("GET", f"/api/docx-info?{broken_query}")
        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "invalid_docx")
        status, payload, _ = self.request("GET", f"/api/docx-file?{broken_query}")
        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "invalid_docx")

        wrong_query = urllib.parse.urlencode({"path": "plain.bin"})
        status, payload, _ = self.request("GET", f"/api/docx-info?{wrong_query}")
        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "docx_required")

        os.symlink(self.docx_path, self.root / "sample-link.docx")
        link_query = urllib.parse.urlencode({"path": "sample-link.docx"})
        status, payload, _ = self.request("GET", f"/api/docx-file?{link_query}")
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "symlink_forbidden")
        self.assertFalse(self.operation_log.exists())

    def test_natural_listing_traversal_and_symlink_protection(self):
        self.login()
        status, payload, _ = self.request("GET", "/api/list?path=&per_page=100")
        self.assertEqual(status, 200)
        names = [entry["name"] for entry in payload["entries"]]
        self.assertLess(names.index("algo2"), names.index("algo10"))
        link = next(entry for entry in payload["entries"] if entry["name"] == "outside-link")
        self.assertEqual(link["kind"], "symlink")

        bad_paths = ["../outside.txt", "/etc/passwd", "algo2/../outside.txt", "algo2\\hello.txt"]
        for bad in bad_paths:
            query = urllib.parse.urlencode({"path": bad})
            status, _payload, _ = self.request("GET", f"/api/list?{query}")
            self.assertEqual(status, 400, bad)

        query = urllib.parse.urlencode({"path": "outside-link"})
        status, payload, _ = self.request("GET", f"/api/file?{query}")
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "symlink_forbidden")

    def test_csrf_mkdir_upload_preview_range_and_delete(self):
        self.login()
        status, payload, headers = self.request(
            "POST", "/api/mkdir", {"path": "", "name": "newdir"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "csrf_failed")
        self.assertEqual(headers.get("Connection"), "close")

        status, payload, _ = self.request(
            "POST", "/api/mkdir", {"path": "", "name": "newdir"}, self.csrf_headers()
        )
        self.assertEqual(status, 201)
        self.assertTrue((self.root / "newdir").is_dir())

        query = urllib.parse.urlencode({"path": "newdir", "filename": "hello.txt", "conflict": "rename"})
        status, payload, _ = self.request(
            "POST", f"/api/upload?{query}", b"first", {**self.csrf_headers(), "Content-Type": "application/octet-stream"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["name"], "hello.txt")
        status, payload, _ = self.request(
            "POST", f"/api/upload?{query}", b"second", {**self.csrf_headers(), "Content-Type": "application/octet-stream"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["name"], "hello (1).txt")
        self.assertFalse(any(path.name.startswith(".algorithm-upload-") for path in (self.root / "newdir").iterdir()))

        preview_query = urllib.parse.urlencode({"path": "newdir/hello.txt"})
        status, payload, _ = self.request("GET", f"/api/preview?{preview_query}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["content"], "first")
        self.assertFalse(payload["truncated"])

        status, body, headers = self.request(
            "GET", f"/api/file?{preview_query}", headers={"Range": "bytes=1-3"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"irs")
        self.assertEqual(headers["Content-Range"], "bytes 1-3/5")

        status, payload, _ = self.request(
            "POST", "/api/delete", {"path": "newdir/hello.txt"}, self.csrf_headers()
        )
        self.assertEqual(status, 200)
        self.assertFalse((self.root / "newdir" / "hello.txt").exists())

        status, payload, _ = self.request(
            "POST", "/api/delete", {"path": "newdir"}, self.csrf_headers()
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "directory_not_empty")

    def test_upload_conflicts_limits_and_overwrite_safety(self):
        self.login()
        query = urllib.parse.urlencode({"path": "algo2", "filename": "hello.txt", "conflict": "error"})
        status, payload, _ = self.request(
            "POST", f"/api/upload?{query}", b"x", {**self.csrf_headers(), "Content-Type": "application/octet-stream"}
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "file_exists")
        self.assertEqual((self.root / "algo2" / "hello.txt").read_text(encoding="utf-8"), "hello 世界")

        overwrite = urllib.parse.urlencode({"path": "algo2", "filename": "hello.txt", "conflict": "overwrite"})
        status, payload, _ = self.request(
            "POST", f"/api/upload?{overwrite}", b"replacement", {**self.csrf_headers(), "Content-Type": "application/octet-stream"}
        )
        self.assertEqual(status, 201)
        self.assertEqual((self.root / "algo2" / "hello.txt").read_bytes(), b"replacement")

        too_big = urllib.parse.urlencode({"path": "algo2", "filename": "large.bin", "conflict": "rename"})
        status, payload, headers = self.request(
            "POST", f"/api/upload?{too_big}", b"x" * 1025, {**self.csrf_headers(), "Content-Type": "application/octet-stream"}
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["code"], "file_too_large")
        self.assertEqual(headers.get("Connection"), "close")
        self.assertFalse((self.root / "algo2" / "large.bin").exists())

    def test_root_delete_logout_and_standard_range_errors(self):
        self.login()
        status, payload, _ = self.request(
            "POST", "/api/delete", {"path": ""}, self.csrf_headers()
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "root_forbidden")

        query = urllib.parse.urlencode({"path": "plain.bin"})
        status, body, headers = self.request("GET", f"/api/file?{query}", headers={"Range": "bytes=-0"})
        self.assertEqual(status, 416)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Content-Range"], "bytes */10")

        status, payload, _ = self.request(
            "POST", "/api/logout", {}, self.csrf_headers()
        )
        self.assertEqual(status, 200)
        status, payload, _ = self.request("GET", "/api/list?path=")
        self.assertEqual(status, 401)

    def test_operation_log_only_records_file_and_folder_mutations(self):
        self.login()
        self.request("GET", "/api/list?path=")
        query = urllib.parse.urlencode({"path": "plain.bin", "download": "1"})
        status, body, _ = self.request("GET", f"/api/file?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"0123456789")
        self.assertFalse(self.operation_log.exists())
        status, _payload, _ = self.request(
            "POST",
            "/api/mkdir",
            {"path": "", "name": "log-test"},
            self.csrf_headers(),
        )
        self.assertEqual(status, 201)

        upload_query = urllib.parse.urlencode(
            {"path": "log-test", "filename": "record-me.txt", "conflict": "error"}
        )
        status, _payload, _ = self.request(
            "POST",
            f"/api/upload?{upload_query}",
            b"logged content",
            {**self.csrf_headers(), "Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 201)
        status, _payload, _ = self.request(
            "POST",
            "/api/delete",
            {"path": "log-test/record-me.txt"},
            self.csrf_headers(),
        )
        self.assertEqual(status, 200)
        status, _payload, _ = self.request(
            "POST",
            "/api/delete",
            {"path": "log-test"},
            self.csrf_headers(),
        )
        self.assertEqual(status, 200)

        records = [json.loads(line) for line in self.operation_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            [record["event"] for record in records],
            ["mkdir", "upload", "delete", "delete"],
        )
        self.assertTrue(all(record["result"] == "success" for record in records))
        self.assertTrue(all(record["user"] == "bupt" for record in records))
        self.assertEqual(records[0]["path"], "log-test")
        self.assertEqual(records[1]["path"], "log-test/record-me.txt")
        self.assertEqual(records[2]["path"], "log-test/record-me.txt")
        self.assertEqual(records[3]["path"], "log-test")
        self.assertIn("kind=directory", records[0]["detail"])
        self.assertIn("bytes=14", records[1]["detail"])
        self.assertIn("bytes=14", records[2]["detail"])
        self.assertIn("kind=directory", records[3]["detail"])


class UnitTest(unittest.TestCase):
    def test_password_hash_and_path_helpers(self):
        iterations, salt, digest = parse_password_hash(
            "pbkdf2_sha256$100000$12345678$" + "00" * 32
        )
        self.assertEqual(iterations, 100000)
        self.assertEqual(salt, b"12345678")
        self.assertEqual(len(digest), 32)
        with self.assertRaises(ValueError):
            parse_password_hash("pbkdf2_sha256$999999999$x$bad")
        self.assertFalse(verify_password("anything", "pbkdf2_sha256$999999999$x$bad"))
        with self.assertRaises(Exception):
            clean_relative_path("a//b")
        long_name = "测" * 85
        self.assertLessEqual(len(collision_name(long_name, 99).encode("utf-8")), 255)


if __name__ == "__main__":
    unittest.main()
