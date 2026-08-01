"""Deterministic source-adapter tests for official German-law acquisition."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    SourceStatus,
)
from aioa_memory_kernel.contracts.serialization import canonical_json_bytes
from aioa_memory_kernel.german_law import acquisition as official


def _zip_bytes(name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(name, payload)
    return output.getvalue()


class _FakeRoot:
    def __init__(self, base: Path) -> None:
        self.root = base / "acquisition"
        self.root.mkdir()
        self.policy = AcquisitionPolicy(
            maximum_root_bytes=64 * 1024 * 1024,
            initial_minimum_free_bytes=2 * 1024 * 1024,
            final_minimum_free_bytes=1024 * 1024,
            maximum_response_bytes=16 * 1024 * 1024,
            maximum_archive_expanded_bytes=16 * 1024 * 1024,
        )
        self.status = SimpleNamespace(
            device_reference=self.policy.expected_device_reference
        )
        self.request_count = 0

    def resolve(self, relative: str) -> Path:
        return self.root / relative

    def _create_empty(self, relative: str) -> None:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=False)

    def write_json_absent(self, relative: str, value: object) -> None:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")

    def append_jsonl(self, relative: str, value: object) -> None:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")

    def root_size(self) -> int:
        return 12_345

    def free_bytes(self) -> int:
        return 987_654_321


class _FakeClient:
    def __init__(
        self,
        root: _FakeRoot,
        payload: Callable[[str, str], bytes],
    ) -> None:
        self.root = root
        self.payload = payload
        self.calls: list[dict[str, object]] = []
        self.bootstrap_delays: list[tuple[str, float]] = []
        self.robots_installs: list[tuple[str, str, bytes, float]] = []

    def prepare_bootstrap_delay(self, host: str, floor: float) -> None:
        self.bootstrap_delays.append((host, floor))

    def install_robots(
        self,
        *,
        host: str,
        robots_url: str,
        body: bytes,
        policy_floor_seconds: float,
    ) -> dict[str, object]:
        self.robots_installs.append(
            (host, robots_url, body, policy_floor_seconds)
        )
        return {
            "host": host,
            "robots_url": robots_url,
            "robots_sha256": hashlib.sha256(body).hexdigest(),
            "effective_delay_seconds": policy_floor_seconds,
        }

    def download(
        self,
        *,
        source_catalog_id: str,
        url: str,
        relative_output_path: str,
        allowed_hosts: frozenset[str],
        allowed_content_types: frozenset[str],
        terms_reference: str,
        license_reference: str,
        robots_reference: str,
        validators: Iterable[Callable[[Path], object]] = (),
        enforce_robots: bool = True,
    ) -> SimpleNamespace:
        call = {
            "source_catalog_id": source_catalog_id,
            "url": url,
            "relative_output_path": relative_output_path,
            "allowed_hosts": allowed_hosts,
            "allowed_content_types": allowed_content_types,
            "terms_reference": terms_reference,
            "license_reference": license_reference,
            "robots_reference": robots_reference,
            "enforce_robots": enforce_robots,
        }
        self.calls.append(call)
        body = self.payload(url, relative_output_path)
        target = self.root.resolve(relative_output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        for validator in validators:
            validator(target)
        self.root.request_count += 1
        return SimpleNamespace(
            relative_output_path=relative_output_path,
            local_sha256=hashlib.sha256(body).hexdigest(),
            byte_length=len(body),
        )


class OfficialGermanLawAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = _FakeRoot(self.base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _default_payload(_url: str, output: str) -> bytes:
        if output.endswith("robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        if output.endswith(".xml"):
            return b"<?xml version='1.0'?><root/>"
        if output.endswith(".zip"):
            return _zip_bytes("record.xml", b"<document/>")
        if output.endswith(".pdf"):
            return b"%PDF-1.7\nsynthetic"
        return b"<html><body>official synthetic metadata</body></html>"

    def _runner(
        self,
        payload: Callable[[str, str], bytes] | None = None,
    ) -> tuple[official.OfficialGermanLawAcquisition, _FakeClient]:
        client = _FakeClient(self.root, payload or self._default_payload)
        return official.OfficialGermanLawAcquisition(self.root, client), client

    def test_gii_live_toc_shape_rewrites_same_host_http_to_https(self) -> None:
        toc = b"""<?xml version='1.0' encoding='UTF-8'?>
<items>
  <item>
    <title>Example Act</title>
    <link>http://www.gesetze-im-internet.de/example_act/xml.zip</link>
  </item>
</items>
"""

        def payload(url: str, output: str) -> bytes:
            if output.endswith("gii-toc.xml"):
                return toc
            if output.endswith("example_act.zip"):
                return _zip_bytes(
                    "example_act.xml",
                    b"<?xml version='1.0'?><dokumente><norm/></dokumente>",
                )
            return self._default_payload(url, output)

        runner, client = self._runner(payload)
        result = runner.run_gii()

        self.assertEqual(result["status"], SourceStatus.COMPLETE.value)
        self.assertEqual(result["target_count"], 1)
        archive_calls = [
            call
            for call in client.calls
            if str(call["relative_output_path"]).endswith("example_act.zip")
        ]
        self.assertEqual(len(archive_calls), 1)
        self.assertEqual(
            archive_calls[0]["url"],
            "https://www.gesetze-im-internet.de/example_act/xml.zip",
        )
        frozen = json.loads(
            self.root.resolve(
                "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/"
                "frozen-current-law-targets.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(frozen["targets"][0]["law_id"], "example_act")
        self.assertEqual(frozen["targets"][0]["index_url"], toc.decode().split("<link>", 1)[1].split("</link>", 1)[0])
        self.assertEqual(
            frozen["targets"][0]["xml_zip_url"], archive_calls[0]["url"]
        )

    def test_gii_empty_live_toc_fails_closed(self) -> None:
        def payload(url: str, output: str) -> bytes:
            if output.endswith("gii-toc.xml"):
                return b"<?xml version='1.0'?><items/>"
            return self._default_payload(url, output)

        runner, _ = self._runner(payload)
        with self.assertRaises(AcquisitionError) as caught:
            runner.run_gii()
        self.assertEqual(caught.exception.code, "IDENTIFIER_MISMATCH")

    def test_gii_percent_encoded_slug_is_rejected(self) -> None:
        toc = b"""<items><item><title>Encoded</title>
<link>https://www.gesetze-im-internet.de/%65xample/xml.zip</link>
</item></items>"""

        def payload(url: str, output: str) -> bytes:
            if output.endswith("gii-toc.xml"):
                return toc
            return self._default_payload(url, output)

        runner, _ = self._runner(payload)
        with self.assertRaises(AcquisitionError) as caught:
            runner.run_gii()
        self.assertEqual(caught.exception.code, "IDENTIFIER_MISMATCH")

    def test_gii_inner_xml_is_validated_without_extraction(self) -> None:
        valid = self.base / "valid.zip"
        valid.write_bytes(_zip_bytes("nested/law.xml", b"<law><section/></law>"))
        summary = official._inspect_gii_xml_archive(valid, "law-1")
        self.assertEqual(summary["law_id"], "law-1")
        self.assertEqual(summary["xml_entry_count"], 1)
        self.assertEqual(summary["xml_members"][0]["root_tag"], "law")

        malformed = self.base / "malformed.zip"
        malformed.write_bytes(_zip_bytes("law.xml", b"<law>"))
        with self.assertRaises(AcquisitionError) as caught:
            official._inspect_gii_xml_archive(malformed, "law-1")
        self.assertEqual(caught.exception.code, "XML_MALFORMED")

        no_xml = self.base / "no-xml.zip"
        no_xml.write_bytes(_zip_bytes("readme.txt", b"metadata"))
        with self.assertRaises(AcquisitionError) as caught:
            official._inspect_gii_xml_archive(no_xml, "law-1")
        self.assertEqual(caught.exception.code, "XML_MALFORMED")

    def test_bgbl_live_sitemap_shape_and_exact_object_classification(self) -> None:
        year = time.gmtime().tm_year
        issue = f"https://www.recht.bund.de/bgbl/1/{year}/42/VO.html"
        sitemap = (
            "<?xml version='1.0'?>"
            "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            f"<url><loc>{issue}</loc></url>"
            "</urlset>"
        ).encode("utf-8")
        issue_page = f"""<html><head>
<base href="https://www.recht.bund.de/" />
</head><body>
<a href="/eli/bund/bgbl-1/{year}/42">ELI</a>
<a href="/bgbl/1/{year}/42/VO.html?view=zipdownload&amp;nn=1">ZIP</a>
<a href="/bgbl/1/{year}/42/regelungstext.pdf?__blob=publicationFile&amp;v=1">Principal</a>
<a href="/bgbl/1/{year}/42/anlage-a.pdf?__blob=publicationFile&amp;v=1">Attachment</a>
<a href="/bgbl/1/{year}/42/not-an-attachment.pdf?download=1">Ignored</a>
</body></html>""".encode("utf-8")

        def payload(url: str, output: str) -> bytes:
            if output.endswith("Sitemap_Verkuendungen.xml"):
                return sitemap
            if output.endswith("BGBl-1-%s-42.html" % year):
                return issue_page
            if output.endswith(".zip"):
                return _zip_bytes("issue.pdf", b"%PDF-1.7\ninside")
            if output.endswith(".pdf"):
                return b"%PDF-1.7\nsynthetic"
            return self._default_payload(url, output)

        runner, client = self._runner(payload)
        result = runner.run_bgbl()

        self.assertEqual(result["status"], SourceStatus.COMPLETE.value)
        self.assertEqual(result["target_count"], 1)
        self.assertEqual(result["bgbl_1_target_count"], 1)
        self.assertEqual(result["bgbl_2_target_count"], 0)
        frozen = json.loads(
            self.root.resolve(
                "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/"
                "frozen-issue-targets-1b.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            frozen["target_policy_version"],
            "bgbl-sitemap-issue-path-1b",
        )
        outputs = [str(call["relative_output_path"]) for call in client.calls]
        self.assertIn(
            f"11_DE_FEDERAL_PROMULGATION_BGBL/issue-zips/BGBl-1-{year}-42.zip",
            outputs,
        )
        self.assertIn(
            f"11_DE_FEDERAL_PROMULGATION_BGBL/pdfs/BGBl-1-{year}-42.pdf",
            outputs,
        )
        self.assertIn(
            "11_DE_FEDERAL_PROMULGATION_BGBL/attachments/"
            f"BGBl-1-{year}-42-attachment-01.pdf",
            outputs,
        )
        self.assertFalse(any("not-an-attachment" in str(call["url"]) for call in client.calls))
        provenance = json.loads(
            self.root.resolve(
                "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/"
                f"BGBl-1-{year}-42-provenance.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            provenance["eli"],
            f"https://www.recht.bund.de/eli/bund/bgbl-1/{year}/42",
        )
        self.assertEqual(
            [record["role"] for record in provenance["pdfs"]],
            ["PRINCIPAL", "ATTACHMENT"],
        )

    def test_bgbl_official_a_suffix_issue_is_not_omitted(self) -> None:
        year = time.gmtime().tm_year
        issue = f"https://www.recht.bund.de/bgbl/1/{year}/210a/VO.html"
        sitemap = (
            "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            f"<url><loc>{issue}</loc></url></urlset>"
        ).encode("utf-8")
        issue_page = f"""<html><head>
<base href="https://www.recht.bund.de/" /></head><body>
<a href="/eli/bund/bgbl-1/{year}/210a">ELI</a>
<a href="/bgbl/1/{year}/210a/VO.html?view=zipdownload">ZIP</a>
<a href="/bgbl/1/{year}/210a/regelungstext.pdf?__blob=publicationFile&amp;v=1">PDF</a>
</body></html>""".encode("utf-8")

        def payload(url: str, output: str) -> bytes:
            if output.endswith("Sitemap_Verkuendungen.xml"):
                return sitemap
            if output.endswith(f"BGBl-1-{year}-210a.html"):
                return issue_page
            if output.endswith(".zip"):
                return _zip_bytes("issue.pdf", b"%PDF-1.7\ninside")
            if output.endswith(".pdf"):
                return b"%PDF-1.7\nsynthetic"
            return self._default_payload(url, output)

        runner, _ = self._runner(payload)
        result = runner.run_bgbl()

        self.assertEqual(result["status"], SourceStatus.COMPLETE.value)
        self.assertEqual(result["target_count"], 1)
        self.assertTrue(
            self.root.resolve(
                f"11_DE_FEDERAL_PROMULGATION_BGBL/pdfs/"
                f"BGBl-1-{year}-210a.pdf"
            ).is_file()
        )

    def test_bgbl_empty_or_non_issue_sitemap_fails_closed(self) -> None:
        sitemap = b"""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
<url><loc>https://www.recht.bund.de/de/home/home_node.html</loc></url>
</urlset>"""

        def payload(url: str, output: str) -> bytes:
            if output.endswith("Sitemap_Verkuendungen.xml"):
                return sitemap
            return self._default_payload(url, output)

        runner, _ = self._runner(payload)
        with self.assertRaises(AcquisitionError) as caught:
            runner.run_bgbl()
        self.assertEqual(caught.exception.code, "IDENTIFIER_MISMATCH")

    def test_bremen_license_gate_blocks_exports(self) -> None:
        def payload(url: str, output: str) -> bytes:
            if output.endswith("dataset-page.html"):
                return b"<html><body>Responsible metadata missing</body></html>"
            return self._default_payload(url, output)

        runner, client = self._runner(payload)
        result = runner.run_bremen()

        self.assertEqual(result["status"], SourceStatus.BLOCKED_CHANGED.value)
        self.assertIn("csv-export:LIVE_DATASET_GATE_FAILED", result["blocked"])
        self.assertIn("xml-export:LIVE_DATASET_GATE_FAILED", result["blocked"])
        self.assertFalse(
            any("/datasets/" in str(call["relative_output_path"]) for call in client.calls)
        )

    def test_bremen_csv_rejects_html_or_non_tabular_exports(self) -> None:
        for name, payload in (
            ("html", b"<html><body>not csv</body></html>"),
            ("single-column", b"only-one-column\nvalue\n"),
            ("invalid-utf8", b"column-a,column-b\n\xff,value\n"),
        ):
            with self.subTest(name=name):
                path = self.base / f"{name}.csv"
                path.write_bytes(payload)
                with self.assertRaises(AcquisitionError) as caught:
                    official._validate_bremen_csv(path)
                self.assertEqual(caught.exception.code, "CONTENT_TYPE_MISMATCH")

    def test_bayern_is_a_safe_metadata_only_skip(self) -> None:
        runner, client = self._runner()
        result = runner.run_bayern()

        self.assertEqual(result["status"], SourceStatus.SKIPPED_SAFE.value)
        self.assertEqual(
            result["reason"], "NO_OFFICIAL_COMPLETE_BOUNDED_ENUMERATION"
        )
        self.assertEqual(result["xml_zips_downloaded"], 0)
        self.assertFalse(
            any("xml-zips" in str(call["relative_output_path"]) for call in client.calls)
        )

    def test_bmf_dip_and_eurlex_make_no_forbidden_request(self) -> None:
        runner, client = self._runner()
        bmf = runner.run_bmf()
        dip = runner.run_dip_plan()
        eurlex = runner.run_eurlex_plan()

        self.assertEqual(bmf["metadata_downloads"], 0)
        self.assertEqual(bmf["pdf_downloads"], 0)
        self.assertEqual(dip["api_requests"], 0)
        self.assertFalse(dip["credentials_created"])
        self.assertEqual(eurlex["downloads"], 0)
        self.assertFalse(eurlex["credentials_created"])
        requested_urls = [str(call["url"]) for call in client.calls]
        self.assertEqual(
            requested_urls,
            [
                "https://dip.bundestag.de/robots.txt",
                "https://dip.bundestag.de/documents/"
                "informationsblatt_zur_dip_api.pdf",
                "https://dip.bundestag.de/documents/nutzungsbedingungen_dip.pdf",
            ],
        )
        self.assertFalse(any("api/v1" in url for url in requested_urls))
        self.assertFalse(any("eur-lex" in url for url in requested_urls))
        self.assertFalse(any("bundesfinanzministerium" in url for url in requested_urls))

    def test_checkpoint_exact_replay_is_stable_and_conflict_free(self) -> None:
        for relative, payload in (
            ("00_CONTROL/request-ledger.jsonl", b"{}\n"),
            ("00_CONTROL/object-ledger.jsonl", b"{}\n"),
            (
                "03_SOURCE_CATALOG/official-source-catalog.jsonl",
                b'{"source_catalog_id":"synthetic"}\n',
            ),
        ):
            target = self.root.resolve(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        runner, _ = self._runner()
        arguments = {
            "source_id": official.GII_ID,
            "prefix": "gii",
            "ordinal": 100,
            "last_completed_object": "law-100",
            "counts": {"completed": 100, "failed": 0, "quarantined": 0},
        }

        runner._checkpoint(**arguments)
        runner._checkpoint(**arguments)

        checkpoints = list(
            self.root.resolve("00_CONTROL/checkpoints").glob("gii-00100-*.json")
        )
        self.assertEqual(len(checkpoints), 1)
        record = json.loads(checkpoints[0].read_text(encoding="utf-8"))
        digest = record.pop("checkpoint_digest")
        self.assertEqual(
            digest,
            hashlib.sha256(canonical_json_bytes(record)).hexdigest(),
        )
        self.assertEqual(record["last_completed_object"], "law-100")


if __name__ == "__main__":
    unittest.main()
