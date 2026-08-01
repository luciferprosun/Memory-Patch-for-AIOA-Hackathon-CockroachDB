"""Fixed official-source adapters for the German-law acquisition plan 1A."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from aioa_memory_kernel.acquisition import (
    AcquisitionError,
    AcquisitionRootGuard,
    SafeHttpsClient,
    SourceStatus,
    validate_pdf,
    validate_xml,
    validate_zip,
)
from aioa_memory_kernel.contracts.serialization import canonical_json_bytes


GII_ID = "DE-GII-CONSOLIDATED-XML-1A"
BGBL_ID = "DE-BGBL-ELECTRONIC-ELI-1A"
BREMEN_ID = "DE-BREMEN-OPEN-METADATA-1A"
BAYERN_ID = "DE-BAYERN-CURRENT-LAW-XML-1A"
BMF_ID = "DE-BMF-GUIDANCE-METADATA-1A"
DIP_ID = "DE-DIP-LEGISLATIVE-PLAN-1A"
EURLEX_ID = "EU-EURLEX-SECTOR3-DATADUMP-PLAN-1A"


SOURCE_CATALOG = (
    {
        "source_catalog_id": GII_ID,
        "publisher": "Federal Ministry of Justice and Consumer Protection / Federal Office of Justice",
        "source_class": "OFFICIAL_BUT_NON_AUTHENTIC_CONSOLIDATED_TEXT",
        "acquisition_class": "A",
        "allowed_hosts": ["www.gesetze-im-internet.de"],
        "canonical_format": "application/zip+xml",
        "automatic_status": "AUTHORIZED",
    },
    {
        "source_catalog_id": BGBL_ID,
        "publisher": "Verkuendungsplattform des Bundes",
        "source_class": "AUTHENTIC_PROMULGATION",
        "acquisition_class": "C",
        "allowed_hosts": ["recht.bund.de", "www.recht.bund.de"],
        "canonical_format": "issue-zip+pdf",
        "automatic_status": "AUTHORIZED_RESUMABLE",
    },
    {
        "source_catalog_id": BREMEN_ID,
        "publisher": "Freie Hansestadt Bremen",
        "source_class": "OFFICIAL_METADATA",
        "acquisition_class": "A",
        "allowed_hosts": ["www.transparenz.bremen.de"],
        "canonical_format": "csv+xml",
        "automatic_status": "CONDITIONAL_ON_LIVE_LICENSE_AND_FORMAT",
    },
    {
        "source_catalog_id": BAYERN_ID,
        "publisher": "Freistaat Bayern",
        "source_class": "OFFICIAL_CONSOLIDATED_STATE_LAW",
        "acquisition_class": "C",
        "allowed_hosts": ["www.gesetze-bayern.de"],
        "canonical_format": "application/zip+xml",
        "automatic_status": "CONDITIONAL_ON_BOUNDED_ENUMERATION",
    },
    {
        "source_catalog_id": BMF_ID,
        "publisher": "Federal Ministry of Finance",
        "source_class": "ADMINISTRATIVE_GUIDANCE",
        "acquisition_class": "D",
        "allowed_hosts": ["www.bundesfinanzministerium.de"],
        "canonical_format": "metadata-only",
        "automatic_status": "BLOCKED_BY_BOT_MANAGER_AND_ROBOTS",
    },
    {
        "source_catalog_id": DIP_ID,
        "publisher": "Deutscher Bundestag / Bundesrat",
        "source_class": "LEGISLATIVE_HISTORY",
        "acquisition_class": "B",
        "allowed_hosts": ["dip.bundestag.de"],
        "canonical_format": "json+xml+pdf",
        "automatic_status": "PLAN_ONLY_API_KEY_REQUIRED",
    },
    {
        "source_catalog_id": EURLEX_ID,
        "publisher": "Publications Office of the European Union",
        "source_class": "EU_OFFICIAL_LEGAL_DATA",
        "acquisition_class": "B",
        "allowed_hosts": ["eur-lex.europa.eu", "datadump.publications.europa.eu"],
        "canonical_format": "formex+xml",
        "automatic_status": "PLAN_ONLY_EU_LOGIN_REQUIRED",
    },
)


EXCLUDED_SOURCES = (
    ("rechtsprechung-im-internet", "ROBOTS_DISALLOWED"),
    ("bundesarbeitsgericht-decisions", "ROBOTS_DISALLOWED"),
    ("bundesverfassungsgericht-historical", "NO_SAFE_BULK_ROUTE"),
    ("bundesgerichtshof-historical", "NO_SAFE_BULK_ROUTE"),
    ("bundespatentgericht-historical", "NO_SAFE_BULK_ROUTE"),
    ("cjeu-infocuria-bulk", "NO_SAFE_BULK_ROUTE"),
    ("saarland-systematic-portal", "SYSTEMATIC_COLLECTION_PROHIBITED"),
    ("rheinland-pfalz-systematic-portal", "TERMS_RESTRICTED"),
    ("sachsen-anhalt-crawler-targets", "ROBOTS_DISALLOWED"),
    ("thueringen-crawler-targets", "ROBOTS_DISALLOWED"),
    ("commercial-juris-portals", "PAID_OR_PRIVATE"),
    ("private-commentary", "PRIVATE_COPYRIGHTED_CONTENT"),
    ("unverified-mirrors", "UNVERIFIED_PROVENANCE"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base_url: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        for key, value in attrs:
            if (
                lowered == "base"
                and key.lower() == "href"
                and isinstance(value, str)
            ):
                self.base_url = value
            elif (
                lowered == "a"
                and key.lower() == "href"
                and isinstance(value, str)
            ):
                self.links.append(value)


def _inspect_gii_xml_archive(path: Path, law_id: str) -> dict[str, object]:
    summary = validate_zip(path)
    xml_members: list[dict[str, object]] = []
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.lower().endswith(".xml"):
                continue
            if entry.file_size > 64 * 1024 * 1024:
                raise AcquisitionError(
                    "GII XML member exceeds its bounded inspection size",
                    code="XML_SIZE_LIMIT_EXCEEDED",
                )
            with archive.open(entry, "r") as stream:
                payload = stream.read(64 * 1024 * 1024 + 1)
            if len(payload) != entry.file_size:
                raise AcquisitionError(
                    "GII XML member length differs from the ZIP directory",
                    code="ZIP_INTEGRITY_FAILED",
                )
            try:
                root = ET.fromstring(payload)
            except ET.ParseError as exc:
                raise AcquisitionError(
                    "GII archive contains malformed XML",
                    code="XML_MALFORMED",
                ) from exc
            xml_members.append(
                {
                    "relative_member_name": entry.filename,
                    "byte_length": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "root_tag": root.tag,
                }
            )
    if not xml_members:
        raise AcquisitionError(
            "GII archive contains no XML document",
            code="XML_MALFORMED",
        )
    return {
        **summary,
        "law_id": law_id,
        "xml_members": xml_members,
    }


def _validate_bremen_csv(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(64 * 1024)
            stream.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            rows = []
            reader = csv.reader(stream, dialect)
            for _ in range(5):
                row = next(reader, None)
                if row is None:
                    break
                rows.append(row)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise AcquisitionError(
            "Bremen CSV export is malformed",
            code="CONTENT_TYPE_MISMATCH",
        ) from exc
    if not rows or len(rows[0]) < 2 or any("<html" in cell.lower() for cell in rows[0]):
        raise AcquisitionError(
            "Bremen CSV export does not have a machine-readable table shape",
            code="CONTENT_TYPE_MISMATCH",
        )
    return {"header_columns": len(rows[0]), "sampled_rows": len(rows)}


class OfficialGermanLawAcquisition:
    def __init__(
        self,
        root: AcquisitionRootGuard,
        client: SafeHttpsClient,
        *,
        maximum_runtime_seconds: int = 0,
    ) -> None:
        self.root = root
        self.client = client
        self.deadline = (
            time.monotonic() + maximum_runtime_seconds
            if maximum_runtime_seconds > 0
            else None
        )
        self.results: dict[str, dict[str, object]] = {}

    def _time_available(self, minimum_seconds: float = 0) -> bool:
        return self.deadline is None or time.monotonic() + minimum_seconds < self.deadline

    def _write_once(self, relative: str, value: object) -> None:
        target = self.root.resolve(relative)
        payload = canonical_json_bytes(value) + b"\n"
        if target.exists():
            if target.read_bytes() != payload:
                raise AcquisitionError(
                    "immutable derived record conflicts on replay",
                    code="ACQUISITION_REPLAY_CONFLICT",
                )
            return
        self.root.write_json_absent(relative, value)

    def _checkpoint(
        self,
        *,
        source_id: str,
        prefix: str,
        ordinal: int,
        last_completed_object: str,
        counts: dict[str, int],
    ) -> None:
        request_ledger = self.root.resolve("00_CONTROL/request-ledger.jsonl")
        object_ledger = self.root.resolve("00_CONTROL/object-ledger.jsonl")
        catalog = self.root.resolve(
            "03_SOURCE_CATALOG/official-source-catalog.jsonl"
        )
        payload = {
            "schema_version": "1.0.0",
            "source_catalog_id": source_id,
            "completed_through_ordinal": ordinal,
            "last_completed_object": last_completed_object,
            "counts": counts,
            "acquisition_policy_digest": self.root.policy.digest,
            "source_catalog_sha256": _sha256(catalog),
            "usb_device_reference": self.root.status.device_reference,
            "acquisition_root_identity": hashlib.sha256(
                (
                    self.root.policy.expected_device_reference
                    + "\0"
                    + self.root.policy.target_relative_path
                ).encode("utf-8")
            ).hexdigest(),
            "seed_sha256": self.root.policy.seed_sha256,
            "request_ledger_sha256": _sha256(request_ledger),
            "object_ledger_sha256": _sha256(object_ledger),
            "created_bytes": self.root.root_size(),
            "free_bytes": self.root.free_bytes(),
        }
        checkpoint_digest = hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest()
        payload["checkpoint_digest"] = checkpoint_digest
        self._write_once(
            "00_CONTROL/checkpoints/"
            f"{prefix}-{ordinal:05d}-{checkpoint_digest[:16]}.json",
            payload,
        )

    def initialize_catalog_and_plans(self) -> None:
        catalog = self.root.resolve(
            "03_SOURCE_CATALOG/official-source-catalog.jsonl"
        )
        if not catalog.exists():
            self.root._create_empty(
                "03_SOURCE_CATALOG/official-source-catalog.jsonl"
            )
            for record in SOURCE_CATALOG:
                self.root.append_jsonl(
                    "03_SOURCE_CATALOG/official-source-catalog.jsonl",
                    record,
                )
        self._write_once(
            "30_LOGIN_GATED_PLANS/dip-api-plan.json",
            {
                "source_catalog_id": DIP_ID,
                "status": "PLAN_ONLY",
                "authentication": "VALID_42_CHARACTER_API_KEY_REQUIRED",
                "credential_fields": ["api_key"],
                "credentials_created": False,
                "documents": [
                    "https://dip.bundestag.de/documents/informationsblatt_zur_dip_api.pdf",
                    "https://dip.bundestag.de/documents/nutzungsbedingungen_dip.pdf",
                ],
                "proposed_scope": "legislation-linked procedures and referenced documents",
                "maximum_concurrency": 1,
                "estimated_record_count": None,
                "estimated_request_bound": 20_000,
                "proposed_storage_root": "30_LOGIN_GATED_PLANS/dip-future-acquisition",
            },
        )
        self._write_once(
            "30_LOGIN_GATED_PLANS/eurlex-sector3-plan.json",
            {
                "source_catalog_id": EURLEX_ID,
                "status": "PLAN_ONLY",
                "authentication": "EU_LOGIN_REQUIRED",
                "credentials_created": False,
                "language": "DEU",
                "celex_sector": "3",
                "filter": "in-force legal acts",
                "documentation": "https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html?locale=en",
                "credential_fields": ["eu_login_session"],
                "estimated_record_count": None,
                "estimated_request_bound": None,
                "proposed_storage_root": "30_LOGIN_GATED_PLANS/eurlex-sector3-future-acquisition",
            },
        )
        for source, reason in EXCLUDED_SOURCES:
            self._write_once(
                f"40_RESEARCH_ONLY_SOURCES/{source}.json",
                {
                    "source_id": source,
                    "automatic_acquisition": False,
                    "reason": reason,
                    "attempts_made": 0,
                },
            )

    def _quarantine(self, source_id: str, url: str, code: str) -> None:
        url_sha256 = hashlib.sha256(url.encode("utf-8")).hexdigest()
        record_id = hashlib.sha256(
            f"{source_id}\0{url_sha256}\0{code}".encode("utf-8")
        ).hexdigest()
        self._write_once(
            f"90_QUARANTINE/records/{record_id}.json",
            {
                "record_id": record_id,
                "source_catalog_id": source_id,
                "url_sha256": url_sha256,
                "reason": code,
                "bytes_deleted": 0,
            },
        )

    def _robots(
        self,
        *,
        source_id: str,
        host: str,
        allowed_hosts: frozenset[str],
        url: str,
        output: str,
        floor: float,
    ) -> None:
        self.client.prepare_bootstrap_delay(host, floor)
        receipt = self.client.download(
            source_catalog_id=source_id,
            url=url,
            relative_output_path=output,
            allowed_hosts=allowed_hosts,
            allowed_content_types=frozenset({"text/plain"}),
            terms_reference=url,
            license_reference=url,
            robots_reference=url,
            enforce_robots=False,
        )
        body = self.root.resolve(receipt.relative_output_path).read_bytes()
        evidence = self.client.install_robots(
            host=host,
            robots_url=url,
            body=body,
            policy_floor_seconds=floor,
        )
        self._write_once(
            f"02_LICENSES_AND_TERMS/{source_id}-robots-policy.json",
            evidence,
        )

    def run_gii(self) -> dict[str, object]:
        host = "www.gesetze-im-internet.de"
        allowed = frozenset({host})
        base = "https://www.gesetze-im-internet.de"
        self._robots(
            source_id=GII_ID,
            host=host,
            allowed_hosts=allowed,
            url=f"{base}/robots.txt",
            output="02_LICENSES_AND_TERMS/gii-robots.txt",
            floor=1.0,
        )
        prerequisites = (
            ("index.html", "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/index.html", {"text/html"}, ()),
            ("hinweise.html", "02_LICENSES_AND_TERMS/gii-hinweise.html", {"text/html"}, ()),
            ("gii-toc.xml", "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/gii-toc.xml", {"application/xml", "text/xml"}, (validate_xml,)),
            ("aktuDienst.html", "10_DE_FEDERAL_CONSOLIDATED_GII/feeds/aktuDienst.html", {"text/html"}, ()),
            ("aktuDienst-rss-feed.xml", "10_DE_FEDERAL_CONSOLIDATED_GII/feeds/aktuDienst-rss-feed.xml", {"application/xml", "text/xml"}, (validate_xml,)),
            ("dtd/1.01/gii-norm.dtd", "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/gii-norm.dtd", {"application/xml-dtd", "text/plain", "application/xml"}, ()),
        )
        for suffix, output, types, validators in prerequisites:
            self.client.download(
                source_catalog_id=GII_ID,
                url=f"{base}/{suffix}",
                relative_output_path=output,
                allowed_hosts=allowed,
                allowed_content_types=frozenset(types),
                terms_reference=f"{base}/hinweise.html",
                license_reference=f"{base}/hinweise.html",
                robots_reference=f"{base}/robots.txt",
                validators=validators,
            )
        toc = self.root.resolve(
            "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/gii-toc.xml"
        )
        try:
            parsed = ET.parse(toc)
        except ET.ParseError as exc:
            raise AcquisitionError(
                "frozen GII index is malformed",
                code="XML_MALFORMED",
            ) from exc
        targets: list[dict[str, str]] = []
        slugs: set[str] = set()
        for item in parsed.getroot().findall("item"):
            title = item.findtext("title")
            link = item.findtext("link")
            if not title or not link:
                raise AcquisitionError(
                    "GII index item is incomplete",
                    code="IDENTIFIER_MISMATCH",
                )
            parsed_url = urllib.parse.urlsplit(link)
            if (
                parsed_url.scheme not in {"http", "https"}
                or parsed_url.hostname != host
                or not parsed_url.path.endswith("/xml.zip")
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise AcquisitionError(
                    "GII index escaped its fixed XML host/path policy",
                    code="URL_NOT_ALLOWLISTED",
                )
            parts = [part for part in parsed_url.path.split("/") if part]
            if len(parts) != 2 or parts[1] != "xml.zip":
                raise AcquisitionError(
                    "GII XML path is not one law identifier",
                    code="IDENTIFIER_MISMATCH",
                )
            slug = parts[0]
            if not re.fullmatch(r"[A-Za-z0-9._~-]{1,200}", slug) or slug in slugs:
                raise AcquisitionError(
                    "GII law identifier is malformed or duplicated",
                    code="IDENTIFIER_MISMATCH",
                )
            slugs.add(slug)
            targets.append(
                {
                    "law_id": slug,
                    "title": title,
                    "index_url": link,
                    "xml_zip_url": urllib.parse.urlunsplit(
                        ("https", host, parsed_url.path, "", "")
                    ),
                }
            )
        if not 1 <= len(targets) <= 10_000:
            raise AcquisitionError(
                "GII frozen index has an unsafe eligible target count",
                code="IDENTIFIER_MISMATCH",
            )
        self._write_once(
            "10_DE_FEDERAL_CONSOLIDATED_GII/indexes/frozen-current-law-targets.json",
            {
                "source_catalog_id": GII_ID,
                "index_sha256": _sha256(toc),
                "target_count": len(targets),
                "targets": targets,
            },
        )
        completed = failed = quarantined = 0
        raw_bytes = 0
        checkpoint_request_count = self.root.request_count
        for ordinal, target in enumerate(targets, start=1):
            if not self._time_available(2.0):
                result = {
                    "status": SourceStatus.PARTIAL.value,
                    "target_count": len(targets),
                    "completed": completed,
                    "failed": failed,
                    "quarantined": quarantined,
                    "raw_bytes": raw_bytes,
                    "next_ordinal": ordinal,
                }
                self.results[GII_ID] = result
                return result
            law_id = target["law_id"]
            archive_summary: dict[str, object] = {}

            def zip_validator(path: Path) -> None:
                archive_summary.update(
                    _inspect_gii_xml_archive(path, law_id)
                )

            try:
                receipt = self.client.download(
                    source_catalog_id=GII_ID,
                    url=target["xml_zip_url"],
                    relative_output_path=(
                        "10_DE_FEDERAL_CONSOLIDATED_GII/xml-zips/"
                        f"{law_id}.zip"
                    ),
                    allowed_hosts=allowed,
                    allowed_content_types=frozenset(
                        {"application/zip", "application/octet-stream"}
                    ),
                    terms_reference=f"{base}/hinweise.html",
                    license_reference=f"{base}/hinweise.html",
                    robots_reference=f"{base}/robots.txt",
                    validators=(zip_validator,),
                )
                completed += 1
                raw_bytes += receipt.byte_length
                if not archive_summary:
                    archive_summary.update(
                        _inspect_gii_xml_archive(
                            self.root.resolve(receipt.relative_output_path),
                            law_id,
                        )
                    )
                self._write_once(
                    "10_DE_FEDERAL_CONSOLIDATED_GII/extracted-metadata/"
                    f"{law_id}.json",
                    {
                        **target,
                        "ordinal": ordinal,
                        "raw_sha256": receipt.local_sha256,
                        "raw_byte_length": receipt.byte_length,
                        "archive": archive_summary,
                    },
                )
            except AcquisitionError as exc:
                failed += 1
                quarantined += 1
                self._quarantine(GII_ID, target["xml_zip_url"], exc.code)
            if (
                ordinal % 100 == 0
                and self.root.request_count > checkpoint_request_count
            ):
                self._checkpoint(
                    source_id=GII_ID,
                    prefix="gii",
                    ordinal=ordinal,
                    last_completed_object=law_id,
                    counts={
                        "completed": completed,
                        "failed": failed,
                        "quarantined": quarantined,
                    },
                )
                checkpoint_request_count = self.root.request_count
        result = {
            "status": (
                SourceStatus.COMPLETE.value
                if completed == len(targets) and failed == 0
                else SourceStatus.FAILED.value
            ),
            "index_sha256": _sha256(toc),
            "target_count": len(targets),
            "completed": completed,
            "failed": failed,
            "quarantined": quarantined,
            "raw_bytes": raw_bytes,
        }
        self.results[GII_ID] = result
        return result

    @staticmethod
    def _sitemap_urls(path: Path) -> list[str]:
        namespace = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise AcquisitionError(
                "BGBl sitemap is malformed",
                code="XML_MALFORMED",
            ) from exc
        return [
            text.strip()
            for text in (node.text for node in root.findall(f"{namespace}url/{namespace}loc"))
            if isinstance(text, str) and text.strip()
        ]

    def run_bgbl(self) -> dict[str, object]:
        hosts = frozenset({"recht.bund.de", "www.recht.bund.de"})
        base = "https://www.recht.bund.de"
        self._robots(
            source_id=BGBL_ID,
            host="www.recht.bund.de",
            allowed_hosts=hosts,
            url=f"{base}/robots.txt",
            output="02_LICENSES_AND_TERMS/recht-bund-robots.txt",
            floor=30.0,
        )
        # Bare-host redirects are allowed, but policy is installed separately
        # so a final bare-host URL remains governed too.
        robots_body = self.root.resolve(
            "02_LICENSES_AND_TERMS/recht-bund-robots.txt"
        ).read_bytes()
        self.client.install_robots(
            host="recht.bund.de",
            robots_url="https://recht.bund.de/robots.txt",
            body=robots_body,
            policy_floor_seconds=30.0,
        )
        for url, output, types, validators in (
            (f"{base}/de/home/home_node.html", "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/home.html", {"text/html"}, ()),
            (f"{base}/de/service/webservice/webservice_node.html", "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/webservice.html", {"text/html"}, ()),
            (f"{base}/XMLSitemaps/Sitemap_Index.xml", "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/Sitemap_Index.xml", {"text/xml", "application/xml"}, (validate_xml,)),
            (f"{base}/XMLSitemaps/Sitemap_Verkuendungen.xml", "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/Sitemap_Verkuendungen.xml", {"text/xml", "application/xml"}, (validate_xml,)),
        ):
            if not self._time_available(31):
                result = {"status": SourceStatus.PARTIAL.value, "completed_issues": 0, "next_ordinal": 1}
                self.results[BGBL_ID] = result
                return result
            self.client.download(
                source_catalog_id=BGBL_ID,
                url=url,
                relative_output_path=output,
                allowed_hosts=hosts,
                allowed_content_types=frozenset(types),
                terms_reference=f"{base}/de/service/webservice/webservice_node.html",
                license_reference="https://www.gesetze-im-internet.de/urhg/__5.html",
                robots_reference=f"{base}/robots.txt",
                validators=validators,
            )
        sitemap = self.root.resolve(
            "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/Sitemap_Verkuendungen.xml"
        )
        current_year = time.gmtime().tm_year
        pattern = re.compile(
            r"^https://(?:www\.)?recht\.bund\.de/bgbl/([12])/"
            r"(20(?:2[3-9]|[3-9][0-9]))/([0-9]{1,6}a?)/VO\.html$"
        )
        targets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        sitemap_urls = self._sitemap_urls(sitemap)
        unmatched: list[str] = []
        for url in sitemap_urls:
            match = pattern.fullmatch(url)
            if match is None:
                unmatched.append(url)
                continue
            part, year, number = match.groups()
            if not 2023 <= int(year) <= current_year:
                continue
            key = (part, year, number)
            if key in seen:
                raise AcquisitionError(
                    "BGBl sitemap contains a duplicated ELI issue",
                    code="IDENTIFIER_MISMATCH",
                )
            seen.add(key)
            targets.append(
                {"part": part, "year": year, "number": number, "issue_url": url}
            )
        def issue_sort_key(item: dict[str, str]) -> tuple[int, int, int, str]:
            issue_match = re.fullmatch(r"([0-9]{1,6})(a?)", item["number"])
            assert issue_match is not None
            numeric, suffix = issue_match.groups()
            return (
                int(item["year"]),
                int(item["part"]),
                int(numeric),
                suffix,
            )

        targets.sort(key=issue_sort_key)
        if not 1 <= len(targets) <= 5_000:
            raise AcquisitionError(
                "BGBl frozen sitemap has an unsafe eligible target count",
                code="IDENTIFIER_MISMATCH",
            )
        self._write_once(
            "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/"
            "frozen-issue-targets-1b.json",
            {
                "source_catalog_id": BGBL_ID,
                "target_policy_version": "bgbl-sitemap-issue-path-1b",
                "sitemap_sha256": _sha256(sitemap),
                "target_count": len(targets),
                "sitemap_url_count": len(sitemap_urls),
                "unmatched_url_count": len(unmatched),
                "unmatched_urls_digest": hashlib.sha256(
                    canonical_json_bytes(unmatched)
                ).hexdigest(),
                "targets": targets,
            },
        )
        completed = partial = quarantined = raw_bytes = 0
        checkpoint_request_count = self.root.request_count
        for ordinal, target in enumerate(targets, start=1):
            if not self._time_available(95):
                result = {
                    "status": SourceStatus.PARTIAL.value,
                    "target_count": len(targets),
                    "completed_issues": completed,
                    "partial_issues": partial,
                    "quarantined": quarantined,
                    "raw_bytes": raw_bytes,
                    "next_ordinal": ordinal,
                }
                self.results[BGBL_ID] = result
                return result
            stem = f"BGBl-{target['part']}-{target['year']}-{target['number']}"
            page_relative = f"11_DE_FEDERAL_PROMULGATION_BGBL/issue-pages/{stem}.html"
            try:
                page_receipt = self.client.download(
                    source_catalog_id=BGBL_ID,
                    url=target["issue_url"],
                    relative_output_path=page_relative,
                    allowed_hosts=hosts,
                    allowed_content_types=frozenset({"text/html"}),
                    terms_reference=f"{base}/de/service/webservice/webservice_node.html",
                    license_reference="https://www.gesetze-im-internet.de/urhg/__5.html",
                    robots_reference=f"{base}/robots.txt",
                )
                raw_bytes += page_receipt.byte_length
                parser = _HrefParser()
                parser.feed(self.root.resolve(page_relative).read_text(encoding="utf-8"))
                if parser.base_url not in {
                    "https://www.recht.bund.de/",
                    "https://recht.bund.de/",
                }:
                    raise AcquisitionError(
                        "BGBl issue page has an unexpected base URL",
                        code="PROVENANCE_INSUFFICIENT",
                    )
                absolute_links = []
                for href in parser.links:
                    candidate = urllib.parse.urljoin(parser.base_url, href)
                    parsed = urllib.parse.urlsplit(candidate)
                    if (
                        parsed.scheme == "https"
                        and parsed.hostname in hosts
                        and parsed.port in (None, 443)
                    ):
                        absolute_links.append(candidate)
                issue_path = (
                    f"/bgbl/{target['part']}/{target['year']}/"
                    f"{target['number']}"
                )
                eli_expected = (
                    "https://www.recht.bund.de/eli/bund/"
                    f"bgbl-{target['part']}/{target['year']}/{target['number']}"
                )
                eli_links = {
                    url.rstrip("/")
                    for url in absolute_links
                    if urllib.parse.urlsplit(url).path.rstrip("/")
                    == (
                        f"/eli/bund/bgbl-{target['part']}/"
                        f"{target['year']}/{target['number']}"
                    )
                }
                if eli_links != {eli_expected}:
                    raise AcquisitionError(
                        "BGBl issue page ELI identity differs",
                        code="IDENTIFIER_MISMATCH",
                    )
                zip_urls: set[str] = set()
                principal_urls: set[str] = set()
                attachment_urls: set[str] = set()
                for url in absolute_links:
                    parsed = urllib.parse.urlsplit(url)
                    query = urllib.parse.parse_qs(
                        parsed.query,
                        keep_blank_values=True,
                    )
                    if (
                        parsed.path == issue_path + "/VO.html"
                        and query.get("view") == ["zipdownload"]
                        and set(query).issubset({"view", "nn"})
                    ):
                        zip_urls.add(url)
                    elif (
                        parsed.path == issue_path + "/regelungstext.pdf"
                        and query.get("__blob") == ["publicationFile"]
                        and set(query).issubset({"__blob", "v"})
                    ):
                        principal_urls.add(url)
                    elif (
                        parsed.path.startswith(issue_path + "/")
                        and parsed.path.lower().endswith(".pdf")
                        and query.get("__blob") == ["publicationFile"]
                        and set(query).issubset({"__blob", "v"})
                    ):
                        attachment_urls.add(url)
                attachment_urls -= principal_urls
                if len(zip_urls) != 1:
                    raise AcquisitionError(
                        "BGBl issue page lacks one exact ZIP download",
                        code="PROVENANCE_INSUFFICIENT",
                    )
                if len(principal_urls) != 1:
                    raise AcquisitionError(
                        "BGBl issue page lacks one exact principal PDF",
                        code="PROVENANCE_INSUFFICIENT",
                    )
                zip_url = next(iter(zip_urls))
                principal_url = next(iter(principal_urls))
                zip_receipt = self.client.download(
                    source_catalog_id=BGBL_ID,
                    url=zip_url,
                    relative_output_path=f"11_DE_FEDERAL_PROMULGATION_BGBL/issue-zips/{stem}.zip",
                    allowed_hosts=hosts,
                    allowed_content_types=frozenset({"application/zip", "application/octet-stream"}),
                    terms_reference=f"{base}/de/service/webservice/webservice_node.html",
                    license_reference="https://www.gesetze-im-internet.de/urhg/__5.html",
                    robots_reference=f"{base}/robots.txt",
                    validators=(lambda path: validate_zip(path, maximum_expanded_bytes=self.root.policy.maximum_archive_expanded_bytes),),
                )
                raw_bytes += zip_receipt.byte_length
                pdf_receipts = []
                for pdf_ordinal, pdf_url in enumerate(
                    (principal_url, *sorted(attachment_urls)),
                    start=1,
                ):
                    if not self._time_available(31):
                        partial += 1
                        break
                    category = "pdfs" if pdf_ordinal == 1 else "attachments"
                    suffix = "" if pdf_ordinal == 1 else f"-attachment-{pdf_ordinal - 1:02d}"
                    receipt = self.client.download(
                        source_catalog_id=BGBL_ID,
                        url=pdf_url,
                        relative_output_path=f"11_DE_FEDERAL_PROMULGATION_BGBL/{category}/{stem}{suffix}.pdf",
                        allowed_hosts=hosts,
                        allowed_content_types=frozenset({"application/pdf", "application/octet-stream"}),
                        terms_reference=f"{base}/de/service/webservice/webservice_node.html",
                        license_reference="https://www.gesetze-im-internet.de/urhg/__5.html",
                        robots_reference=f"{base}/robots.txt",
                        validators=(validate_pdf,),
                    )
                    raw_bytes += receipt.byte_length
                    pdf_receipts.append(
                        {
                            "role": "PRINCIPAL" if pdf_ordinal == 1 else "ATTACHMENT",
                            "url": pdf_url,
                            "sha256": receipt.local_sha256,
                            "byte_length": receipt.byte_length,
                        }
                    )
                else:
                    self._write_once(
                        "11_DE_FEDERAL_PROMULGATION_BGBL/indexes/"
                        f"{stem}-provenance.json",
                        {
                            **target,
                            "eli": eli_expected,
                            "page_sha256": page_receipt.local_sha256,
                            "zip_sha256": zip_receipt.local_sha256,
                            "pdfs": pdf_receipts,
                            "publisher_checksum": None,
                            "publisher_checksum_status": "NOT_PUBLISHED_ON_ISSUE_PAGE",
                        },
                    )
                    completed += 1
            except AcquisitionError as exc:
                partial += 1
                quarantined += 1
                self._quarantine(BGBL_ID, target["issue_url"], exc.code)
            if (
                ordinal % 10 == 0
                and self.root.request_count > checkpoint_request_count
            ):
                self._checkpoint(
                    source_id=BGBL_ID,
                    prefix="bgbl",
                    ordinal=ordinal,
                    last_completed_object=stem,
                    counts={
                        "completed_issues": completed,
                        "partial_issues": partial,
                        "quarantined": quarantined,
                    },
                )
                checkpoint_request_count = self.root.request_count
        result = {
            "status": SourceStatus.COMPLETE.value if completed == len(targets) and partial == 0 else SourceStatus.FAILED.value,
            "sitemap_sha256": _sha256(sitemap),
            "target_count": len(targets),
            "bgbl_1_target_count": sum(1 for item in targets if item["part"] == "1"),
            "bgbl_2_target_count": sum(1 for item in targets if item["part"] == "2"),
            "completed_issues": completed,
            "partial_issues": partial,
            "quarantined": quarantined,
            "raw_bytes": raw_bytes,
        }
        self.results[BGBL_ID] = result
        return result

    def run_bremen(self) -> dict[str, object]:
        host = "www.transparenz.bremen.de"
        allowed = frozenset({host})
        base = f"https://{host}"
        self._robots(
            source_id=BREMEN_ID,
            host=host,
            allowed_hosts=allowed,
            url=f"{base}/robots.txt",
            output="02_LICENSES_AND_TERMS/bremen-robots.txt",
            floor=1.0,
        )
        completed: list[str] = []
        blocked: list[str] = []
        page_evidence: dict[str, dict[str, object]] = {}
        pages = (
            ("csv-page", f"{base}/daten/gesetze-rechtsvorschriften-verordnungen-rundschreiben-und-satzungen-des-landes-bremen-57868", "12_DE_STATE_BREMEN_METADATA/provenance/csv-dataset-page.html"),
            ("xml-page", f"{base}/daten/gesetze-und-rechtsverordnungen-bremen-8261", "12_DE_STATE_BREMEN_METADATA/provenance/xml-dataset-page.html"),
        )
        for name, url, output in pages:
            try:
                receipt = self.client.download(
                    source_catalog_id=BREMEN_ID,
                    url=url,
                    relative_output_path=output,
                    allowed_hosts=allowed,
                    allowed_content_types=frozenset({"text/html"}),
                    terms_reference=url,
                    license_reference=url,
                    robots_reference=f"{base}/robots.txt",
                )
                page = self.root.resolve(receipt.relative_output_path)
                text = page.read_text(encoding="utf-8").lower()
                license_proven = (
                    "creativecommons.org/licenses/by/3.0" in text
                    or "cc by 3.0" in text
                )
                authority_proven = any(
                    marker in text
                    for marker in (
                        "zuständige stelle",
                        "zustaendige stelle",
                        "verantwortliche stelle",
                    )
                )
                publisher_hashes = sorted(
                    set(re.findall(r"(?<![0-9a-f])[0-9a-f]{56}(?![0-9a-f])", text))
                )
                page_evidence[name] = {
                    "page_sha256": receipt.local_sha256,
                    "license_proven": license_proven,
                    "responsible_authority_proven": authority_proven,
                    "publisher_hashes": publisher_hashes,
                    "publisher_hash_algorithm": "UNKNOWN",
                }
                if not license_proven or not authority_proven:
                    blocked.append(f"{name}:LICENSE_OR_AUTHORITY_UNPROVEN")
                else:
                    completed.append(name)
            except AcquisitionError as exc:
                blocked.append(f"{name}:{exc.code}")
        exports = (
            ("csv-export", f"{base}/sixcms/detail.php?template=30_export_template_ifg_csv_d", "12_DE_STATE_BREMEN_METADATA/datasets/bremen-legal-metadata.csv", frozenset({"text/csv", "text/plain", "application/octet-stream"}), (_validate_bremen_csv,)),
            ("xml-export", f"{base}/sixcms/detail.php?dt=Gesetze+und+Rechtsverordnungen&template=30_export_template_ifg_d", "12_DE_STATE_BREMEN_METADATA/datasets/bremen-current-law.xml", frozenset({"application/xml", "text/xml"}), (validate_xml,)),
        )
        for name, url, output, types, validators in exports:
            page_name = "csv-page" if name.startswith("csv") else "xml-page"
            page = page_evidence.get(page_name, {})
            if not (
                page.get("license_proven") is True
                and page.get("responsible_authority_proven") is True
            ):
                blocked.append(f"{name}:LIVE_DATASET_GATE_FAILED")
                continue
            try:
                self.client.download(
                    source_catalog_id=BREMEN_ID,
                    url=url,
                    relative_output_path=output,
                    allowed_hosts=allowed,
                    allowed_content_types=types,
                    terms_reference=pages[0][1] if name.startswith("csv") else pages[1][1],
                    license_reference="CC-BY-3.0",
                    robots_reference=f"{base}/robots.txt",
                    validators=validators,
                )
                completed.append(name)
            except AcquisitionError as exc:
                blocked.append(f"{name}:{exc.code}")
                self._quarantine(BREMEN_ID, url, exc.code)
        result = {
            "status": SourceStatus.COMPLETE.value if not blocked else SourceStatus.BLOCKED_CHANGED.value,
            "completed": completed,
            "blocked": blocked,
            "all_portal_export": "SKIPPED_SCOPE_AND_LICENSE_RISK",
            "license": "CC-BY-3.0_FOR_ACCEPTED_DATASET_PAGES_ONLY",
            "dataset_page_evidence": page_evidence,
        }
        self.results[BREMEN_ID] = result
        return result

    def run_bayern(self) -> dict[str, object]:
        host = "www.gesetze-bayern.de"
        allowed = frozenset({host})
        base = f"https://{host}"
        self._robots(
            source_id=BAYERN_ID,
            host=host,
            allowed_hosts=allowed,
            url=f"{base}/robots.txt",
            output="02_LICENSES_AND_TERMS/bayern-robots.txt",
            floor=1.0,
        )
        evidence: dict[str, dict[str, object]] = {}
        for suffix, name in (
            ("Content/Document/Nutzungshinweise", "terms"),
            ("Content/Document/Hilfe", "help"),
        ):
            try:
                receipt = self.client.download(
                    source_catalog_id=BAYERN_ID,
                    url=f"{base}/{suffix}",
                    relative_output_path=f"13_DE_STATE_BAYERN_CURRENT/indexes/{name}.html",
                    allowed_hosts=allowed,
                    allowed_content_types=frozenset({"text/html"}),
                    terms_reference=f"{base}/Content/Document/Nutzungshinweise",
                    license_reference=f"{base}/Content/Document/Nutzungshinweise",
                    robots_reference=f"{base}/robots.txt",
                )
                text = self.root.resolve(receipt.relative_output_path).read_text(
                    encoding="utf-8"
                ).lower()
                evidence[name] = {
                    "sha256": receipt.local_sha256,
                    "reuse_signal": any(
                        marker in text
                        for marker in ("weiterverwendung", "vervielfältigung", "nutzung")
                    ),
                    "xml_zip_signal": "xml" in text and "zip" in text,
                }
            except AcquisitionError as exc:
                evidence[name] = {"error": exc.code}
        result = {
            "status": SourceStatus.SKIPPED_SAFE.value,
            "enumeration_proven": False,
            "reuse_proven": evidence.get("terms", {}).get("reuse_signal") is True,
            "xml_export_proven": evidence.get("help", {}).get("xml_zip_signal") is True,
            "reason": "NO_OFFICIAL_COMPLETE_BOUNDED_ENUMERATION",
            "xml_zips_downloaded": 0,
            "evidence": evidence,
        }
        self.results[BAYERN_ID] = result
        return result

    def run_bmf(self) -> dict[str, object]:
        result = {
            "status": SourceStatus.BLOCKED_CHANGED.value,
            "reason": "BOT_MANAGER_CROSS_HOST_REDIRECT_AND_ROBOTS_RESTRICTED_RSS",
            "effective_crawl_delay_seconds": 180,
            "metadata_downloads": 0,
            "pdf_downloads": 0,
            "further_attempts": 0,
        }
        self.results[BMF_ID] = result
        return result

    def run_dip_plan(self) -> dict[str, object]:
        host = "dip.bundestag.de"
        allowed = frozenset({host})
        base = f"https://{host}"
        completed: list[str] = []
        blocked: list[str] = []
        try:
            self._robots(
                source_id=DIP_ID,
                host=host,
                allowed_hosts=allowed,
                url=f"{base}/robots.txt",
                output="02_LICENSES_AND_TERMS/dip-robots.txt",
                floor=1.0,
            )
            for name, suffix in (
                (
                    "api-information",
                    "documents/informationsblatt_zur_dip_api.pdf",
                ),
                ("terms", "documents/nutzungsbedingungen_dip.pdf"),
            ):
                self.client.download(
                    source_catalog_id=DIP_ID,
                    url=f"{base}/{suffix}",
                    relative_output_path=(
                        f"30_LOGIN_GATED_PLANS/dip-{name}.pdf"
                    ),
                    allowed_hosts=allowed,
                    allowed_content_types=frozenset(
                        {"application/pdf", "application/octet-stream"}
                    ),
                    terms_reference=f"{base}/documents/nutzungsbedingungen_dip.pdf",
                    license_reference=f"{base}/documents/nutzungsbedingungen_dip.pdf",
                    robots_reference=f"{base}/robots.txt",
                    validators=(validate_pdf,),
                )
                completed.append(name)
        except AcquisitionError as exc:
            blocked.append(exc.code)
        result = {
            "status": SourceStatus.SKIPPED_SAFE.value,
            "reason": "API_KEY_REQUIRED_AND_CREDENTIAL_USE_NOT_AUTHORIZED",
            "credentials_created": False,
            "api_requests": 0,
            "documentation_downloads": completed,
            "documentation_blocked": blocked,
        }
        self.results[DIP_ID] = result
        return result

    def run_eurlex_plan(self) -> dict[str, object]:
        result = {
            "status": SourceStatus.SKIPPED_SAFE.value,
            "reason": "EU_LOGIN_REQUIRED_AND_LOGIN_NOT_AUTHORIZED",
            "credentials_created": False,
            "downloads": 0,
        }
        self.results[EURLEX_ID] = result
        return result


__all__ = [
    "BAYERN_ID",
    "BGBL_ID",
    "BMF_ID",
    "BREMEN_ID",
    "DIP_ID",
    "EURLEX_ID",
    "GII_ID",
    "OfficialGermanLawAcquisition",
    "SOURCE_CATALOG",
]
