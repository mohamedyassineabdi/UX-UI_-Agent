from __future__ import annotations

import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw

from figma_audit.annotations import annotate_issue_screenshots, calculate_rectangle_pixels
from figma_audit.ai_reviewer import issue_needs_ai_review, review_issues_with_ollama
from figma_audit.audit.context import AuditContext
from figma_audit.audit.audit_runner import run_audit
from figma_audit.browser_screenshots import capture_real_page_screenshots
from figma_audit.criteria_matrix import checks_by_axis
from figma_audit.criteria_catalog import load_criteria_catalog
from figma_audit.detections import run_detections
from figma_audit.evidence_quality import attach_evidence_quality
from figma_audit.extraction import build_audit_extraction, normalized_file_from_audit_extraction
from figma_audit.ingestion.fetch_service import fetch_figma_bundle
from figma_audit.ingestion.figma_client import FigmaClient, FigmaRateLimitError
from figma_audit.ingestion.url_parser import parse_figma_url
from figma_audit.models.audit_result import AuditResult
from figma_audit.models.detection import (
    CriterionDetectionStatus,
    DetectionResult,
    DetectionRunSummary,
)
from figma_audit.models.issue import AuditIssue, IssueLocation, Severity
from figma_audit.models.normalized_models import NormalizedFigmaFile
from figma_audit.models.normalized_models import NormalizedNode
from figma_audit.models.raw_bundle import RawFigmaBundle
from figma_audit.normalization.normalizer import normalize_figma_bundle
from figma_audit.pipeline import run_pipeline
from figma_audit.report_polisher import polish_report_copy_with_ollama
from figma_audit.reports import build_detection_review_report
from figma_audit.utils.io import load_json, save_json


class UrlParserTests(unittest.TestCase):
    def test_parse_figma_design_url_with_node_id(self) -> None:
        parsed = parse_figma_url(
            "https://www.figma.com/design/ABC123/My-File?node-id=12-34"
        )

        self.assertEqual(parsed["file_key"], "ABC123")
        self.assertEqual(parsed["node_id"], "12:34")
        self.assertEqual(parsed["url_type"], "design")

    def test_parse_embedded_proto_url_with_starting_point_node_id(self) -> None:
        parsed = parse_figma_url(
            "https://www.figma.com/embed?embed_host=share&url="
            "https%3A%2F%2Fwww.figma.com%2Fproto%2FABC123%2FPrototype"
            "%3Fstarting-point-node-id%3D12-34"
        )

        self.assertEqual(parsed["file_key"], "ABC123")
        self.assertEqual(parsed["node_id"], "12:34")
        self.assertEqual(parsed["url_type"], "proto")

    def test_rejects_non_figma_host(self) -> None:
        with self.assertRaises(ValueError):
            parse_figma_url("https://figma.com.evil/design/ABC123/File")


class CriteriaCatalogTests(unittest.TestCase):
    def test_default_catalog_loads(self) -> None:
        catalog = load_criteria_catalog()

        self.assertEqual(len(catalog.criteria), 7)
        self.assertEqual(catalog.criteria_ids()[0], "task_execution")
        self.assertFalse(catalog.validate_links())

    def test_visible_criteria_matrix_has_important_and_secondary_checks_per_axis(self) -> None:
        grouped = checks_by_axis()
        allowed_methods = {"rule", "ai_assisted", "human_review"}
        ai_review_axes = {
            "flow_architecture",
            "visual_brand",
            "content_microcopy",
        }

        self.assertEqual(set(grouped), set(load_criteria_catalog().criteria_ids()))
        for axis_id, checks in grouped.items():
            with self.subTest(axis_id=axis_id):
                self.assertGreaterEqual(len(checks), 8)
                self.assertGreaterEqual(
                    len([check for check in checks if check.priority == "important"]),
                    5,
                )
                self.assertGreaterEqual(
                    len([check for check in checks if check.priority != "important"]),
                    1,
                )
                self.assertTrue(all(check.visible_rule for check in checks))
                self.assertTrue(
                    all(check.analysis_method in allowed_methods for check in checks)
                )
                if axis_id in ai_review_axes:
                    self.assertTrue(
                        any(check.analysis_method == "ai_assisted" for check in checks)
                    )


class EvidenceQualityTests(unittest.TestCase):
    def test_evidence_quality_builds_portable_criteria_evaluation_grid(self) -> None:
        text_node = NormalizedNode(
            id="text:1",
            name="Primary label",
            type="TEXT",
            path="Screen > Primary label",
            depth=1,
            characters="Send",
            absolute_bounding_box={"x": 0, "y": 0, "width": 80, "height": 24},
        )
        issue = AuditIssue(
            id="issue-1",
            axis="trust_accessibility",
            criterion="trust_accessibility",
            severity=Severity.HIGH,
            message="Text contrast appears below the recommended threshold for its size.",
            location=IssueLocation(node_id="text:1", node_name="Primary label"),
            evidence={
                "detector_id": "low_text_contrast",
                "confidence": "high",
                "contrast_ratio": 2.2,
                "required_ratio": 4.5,
                "resolved_foreground_rgb": [0.7, 0.7, 0.7],
                "resolved_background_rgb": [1.0, 1.0, 1.0],
            },
        )
        detection_result = DetectionResult(
            criterion_status=[
                CriterionDetectionStatus(
                    criterion_id="trust_accessibility",
                    exists=True,
                    issue_count=1,
                    confidence="high",
                    detector_ids=["low_text_contrast"],
                )
            ],
            draft_issues=[issue],
            summary=DetectionRunSummary(
                criteria_total=7,
                criteria_with_detected_problems=1,
                draft_issue_count=1,
                screenshot_count=0,
            ),
        )

        quality = attach_evidence_quality(
            normalized_file=NormalizedFigmaFile(file_key="ABC123", nodes=[text_node]),
            detection_result=detection_result,
            source_url="https://www.figma.com/design/ABC123/File?node-id=1-1",
            node_id="1:1",
        )

        readability_checks = [
            item
            for item in quality["criteria_evaluations"]
            if item["criterion_id"] == "trust_accessibility"
            and item["name"] == "Primary content readability"
        ]
        self.assertEqual(len(readability_checks), 1)
        self.assertEqual(readability_checks[0]["status"], "needs_improvement")
        self.assertEqual(readability_checks[0]["matched_issue_ids"], ["issue-1"])
        self.assertEqual(issue.evidence["evidence_quality"]["quality"], "moderate")
        self.assertGreater(quality["evaluation_summary"]["total_checks"], 0)
        self.assertEqual(quality["scope"]["source_scope"], "selected_node")


class AuditRunnerTests(unittest.TestCase):
    def test_audit_runner_is_disabled_for_now(self) -> None:
        result = run_audit(NormalizedFigmaFile(file_key="test"))

        self.assertEqual(result.total_issues(), 0)
        self.assertEqual(result.issues, [])


class AuditContextTests(unittest.TestCase):
    def test_visible_nodes_excludes_children_of_hidden_parents(self) -> None:
        hidden_parent = NormalizedNode(
            id="frame:1",
            name="Hidden frame",
            type="FRAME",
            path="Hidden frame",
            depth=0,
            visible=False,
        )
        visible_child_in_hidden_parent = NormalizedNode(
            id="text:1",
            name="Visible child",
            type="TEXT",
            parent_id="frame:1",
            path="Hidden frame > Visible child",
            depth=1,
            characters="Delete",
        )
        visible_frame = NormalizedNode(
            id="frame:2",
            name="Visible frame",
            type="FRAME",
            path="Visible frame",
            depth=0,
        )

        ctx = AuditContext(
            NormalizedFigmaFile(
                file_key="file123",
                nodes=[hidden_parent, visible_child_in_hidden_parent, visible_frame],
            )
        )

        self.assertEqual([node.id for node in ctx.visible_nodes], ["frame:2"])

    def test_mobile_viewport_roots_ignore_component_set_variants(self) -> None:
        nodes = [
            NormalizedNode(
                id="0:1",
                name="Canvas",
                type="CANVAS",
                path="Canvas",
                depth=0,
            ),
            NormalizedNode(
                id="screen:1",
                name="Feedback",
                type="FRAME",
                parent_id="0:1",
                path="Canvas > Feedback",
                depth=1,
                absolute_bounding_box={"x": 100, "y": 100, "width": 400, "height": 620},
            ),
            NormalizedNode(
                id="set:1",
                name="Rating Scale",
                type="COMPONENT_SET",
                parent_id="0:1",
                path="Canvas > Rating Scale",
                depth=1,
                absolute_bounding_box={"x": 600, "y": 100, "width": 400, "height": 720},
            ),
            NormalizedNode(
                id="variant:1",
                name="Status=Worst",
                type="COMPONENT",
                parent_id="set:1",
                path="Canvas > Rating Scale > Status=Worst",
                depth=2,
                absolute_bounding_box={"x": 620, "y": 120, "width": 360, "height": 120},
            ),
        ]

        ctx = AuditContext(NormalizedFigmaFile(file_key="file123", nodes=nodes))

        self.assertEqual([node.id for node in ctx.mobile_viewport_roots], ["screen:1"])

    def test_mobile_viewport_for_prefers_outer_phone_screen_over_inner_section(self) -> None:
        screen = NormalizedNode(
            id="screen:1",
            name="Feedback",
            type="FRAME",
            path="Feedback",
            depth=0,
            absolute_bounding_box={"x": 0, "y": 0, "width": 400, "height": 620},
        )
        section = NormalizedNode(
            id="section:1",
            name="Section",
            type="FRAME",
            parent_id="screen:1",
            path="Feedback > Section",
            depth=1,
            absolute_bounding_box={"x": 24, "y": 80, "width": 354, "height": 404},
        )
        field = NormalizedNode(
            id="field:1",
            name="Email input",
            type="FRAME",
            parent_id="section:1",
            path="Feedback > Section > Email input",
            depth=2,
            absolute_bounding_box={"x": 32, "y": 150, "width": 340, "height": 45},
        )

        ctx = AuditContext(NormalizedFigmaFile(file_key="file123", nodes=[screen, section, field]))

        self.assertEqual(ctx.mobile_viewport_for(field).id, "screen:1")

    def test_client_visible_nodes_excludes_nodes_covered_by_front_layer(self) -> None:
        screen = NormalizedNode(
            id="screen:1",
            name="Feedback screen",
            type="FRAME",
            path="Feedback screen",
            depth=0,
            absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
        )
        covered_text = NormalizedNode(
            id="text:covered",
            name="Hidden heading",
            type="TEXT",
            parent_id="screen:1",
            path="Feedback screen > Hidden heading",
            depth=1,
            characters="Behind the panel",
            absolute_bounding_box={"x": 80, "y": 150, "width": 160, "height": 24},
        )
        visible_text = NormalizedNode(
            id="text:visible",
            name="Visible heading",
            type="TEXT",
            parent_id="screen:1",
            path="Feedback screen > Visible heading",
            depth=1,
            characters="Above the panel",
            absolute_bounding_box={"x": 80, "y": 40, "width": 160, "height": 24},
        )
        front_panel = NormalizedNode(
            id="panel:1",
            name="White modal panel",
            type="FRAME",
            parent_id="screen:1",
            path="Feedback screen > White modal panel",
            depth=1,
            fills=[
                {
                    "type": "SOLID",
                    "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                }
            ],
            absolute_bounding_box={"x": 40, "y": 110, "width": 300, "height": 420},
        )

        ctx = AuditContext(
            NormalizedFigmaFile(
                file_key="file123",
                nodes=[screen, covered_text, visible_text, front_panel],
            )
        )

        client_visible_ids = {node.id for node in ctx.client_visible_nodes}
        self.assertNotIn("text:covered", client_visible_ids)
        self.assertIn("text:visible", client_visible_ids)
        self.assertIn("panel:1", client_visible_ids)
        self.assertEqual(ctx.front_layer_visible_ratio(covered_text), 0.0)


class FigmaClientTests(unittest.TestCase):
    def setUp(self) -> None:
        FigmaClient._global_token_cooldowns.clear()
        FigmaClient._global_token_cursor = 0

    def test_client_ignores_environment_proxy_by_default(self) -> None:
        client = FigmaClient(token="test-token")

        self.assertFalse(client.session.trust_env)
        self.assertFalse(client.binary_session.trust_env)

    def test_rate_limit_retry_sleep_is_capped(self) -> None:
        class FakeResponse:
            def __init__(
                self,
                status_code: int,
                payload: dict[str, object] | None = None,
                headers: dict[str, str] | None = None,
                text: str = "",
            ) -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.headers = headers or {}
                self.text = text

            def json(self) -> dict[str, object]:
                return self._payload

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, **kwargs: object) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(
                        429,
                        headers={"Retry-After": "9999"},
                        text="rate limited",
                    )
                return FakeResponse(200, payload={"ok": True})

        client = FigmaClient(token="test-token")
        fake_session = FakeSession()
        client.session = fake_session  # type: ignore[assignment]

        with patch(
            "figma_audit.ingestion.figma_client.FIGMA_MAX_RETRY_SLEEP_SECONDS",
            0.25,
        ), patch(
            "figma_audit.ingestion.figma_client.FIGMA_MAX_RETRY_AFTER_SECONDS",
            10000,
        ), patch(
            "figma_audit.ingestion.figma_client.FIGMA_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch("figma_audit.ingestion.figma_client.time.sleep") as sleep:
            result = client._request("GET", "/v1/test", max_retries=2)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_session.calls, 2)
        sleep.assert_called_once_with(0.25)

    def test_long_rate_limit_retry_after_fails_fast(self) -> None:
        class FakeResponse:
            status_code = 429
            headers = {"Retry-After": "85428"}
            text = "rate limited"

            def json(self) -> dict[str, object]:
                return {}

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, **kwargs: object) -> FakeResponse:
                self.calls += 1
                return FakeResponse()

        messages: list[str] = []
        client = FigmaClient(token="test-token", log=messages.append)
        fake_session = FakeSession()
        client.session = fake_session  # type: ignore[assignment]

        with patch("figma_audit.ingestion.figma_client.FIGMA_MAX_RETRY_AFTER_SECONDS", 3600), patch(
            "figma_audit.ingestion.figma_client.FIGMA_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch("figma_audit.ingestion.figma_client.time.sleep") as sleep:
            with self.assertRaises(FigmaRateLimitError):
                client._request("GET", "/v1/test", max_retries=8)

        self.assertEqual(fake_session.calls, 1)
        sleep.assert_not_called()
        self.assertTrue(any("will not clear soon" in message for message in messages))

    def test_multi_token_rate_limit_falls_back_without_sleeping(self) -> None:
        class FakeResponse:
            def __init__(
                self,
                status_code: int,
                payload: dict[str, object] | None = None,
                headers: dict[str, str] | None = None,
                text: str = "",
            ) -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.headers = headers or {}
                self.text = text

            def json(self) -> dict[str, object]:
                return self._payload

        class FakeSession:
            def __init__(self) -> None:
                self.used_tokens: list[str] = []

            def request(self, **kwargs: object) -> FakeResponse:
                headers = kwargs["headers"]
                assert isinstance(headers, dict)
                token = str(headers["X-Figma-Token"])
                self.used_tokens.append(token)
                if len(self.used_tokens) == 1:
                    return FakeResponse(
                        429,
                        headers={"Retry-After": "9999"},
                        text="rate limited",
                    )
                return FakeResponse(200, payload={"ok": True})

        messages: list[str] = []
        client = FigmaClient(tokens=["token-a", "token-b"], log=messages.append)
        fake_session = FakeSession()
        client.session = fake_session  # type: ignore[assignment]

        with patch("figma_audit.ingestion.figma_client.FIGMA_MAX_RETRY_AFTER_SECONDS", 3600), patch(
            "figma_audit.ingestion.figma_client.FIGMA_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch("figma_audit.ingestion.figma_client.time.sleep") as sleep:
            result = client._request("GET", "/v1/test", max_retries=1)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(fake_session.used_tokens), 2)
        self.assertEqual(set(fake_session.used_tokens), {"token-a", "token-b"})
        sleep.assert_not_called()
        self.assertTrue(any("trying another configured token" in message for message in messages))

    def test_rate_limit_retry_uses_dedicated_retry_budget_and_logs_wait(self) -> None:
        class FakeResponse:
            def __init__(
                self,
                status_code: int,
                payload: dict[str, object] | None = None,
                headers: dict[str, str] | None = None,
                text: str = "",
            ) -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.headers = headers or {}
                self.text = text

            def json(self) -> dict[str, object]:
                return self._payload

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, **kwargs: object) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(429, text="rate limited")
                return FakeResponse(200, payload={"ok": True})

        messages: list[str] = []
        client = FigmaClient(token="test-token", log=messages.append)
        fake_session = FakeSession()
        client.session = fake_session  # type: ignore[assignment]

        with patch("figma_audit.ingestion.figma_client.FIGMA_RATE_LIMIT_RETRIES", 2), patch(
            "figma_audit.ingestion.figma_client.FIGMA_MIN_REQUEST_INTERVAL_SECONDS",
            0,
        ), patch("figma_audit.ingestion.figma_client.RETRY_BACKOFF_SECONDS", 0.5), patch(
            "figma_audit.ingestion.figma_client.time.sleep"
        ) as sleep:
            result = client._request("GET", "/v1/test", max_retries=1)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_session.calls, 2)
        sleep.assert_called_once_with(0.5)
        self.assertTrue(any("Figma rate limit hit" in message for message in messages))

    def test_image_url_cache_requests_only_missing_nodes(self) -> None:
        class FakeResponse:
            status_code = 200
            headers: dict[str, str] = {}
            text = ""

            def __init__(self, ids: str) -> None:
                self.ids = ids

            def json(self) -> dict[str, object]:
                return {
                    "images": {
                        node_id: f"https://images.example/{node_id}.png"
                        for node_id in self.ids.split(",")
                    }
                }

        class FakeSession:
            def __init__(self) -> None:
                self.requested_ids: list[str] = []

            def request(self, **kwargs: object) -> FakeResponse:
                params = kwargs["params"]
                assert isinstance(params, dict)
                ids = str(params["ids"])
                self.requested_ids.append(ids)
                return FakeResponse(ids)

        client = FigmaClient(token="test-token")
        fake_session = FakeSession()
        client.session = fake_session  # type: ignore[assignment]

        with patch("figma_audit.ingestion.figma_client.FIGMA_MIN_REQUEST_INTERVAL_SECONDS", 0):
            first = client.get_image_urls("file123", ["1:1", "2:2"])
            second = client.get_image_urls("file123", ["2:2", "3:3"])

        self.assertEqual(fake_session.requested_ids, ["1:1,2:2", "3:3"])
        self.assertEqual(first["1:1"], "https://images.example/1:1.png")
        self.assertEqual(second["2:2"], "https://images.example/2:2.png")
        self.assertEqual(second["3:3"], "https://images.example/3:3.png")


class FigmaFetchServiceTests(unittest.TestCase):
    def test_node_fetch_preserves_file_metadata_for_versioned_caches(self) -> None:
        class FakeClient:
            def __init__(self, log: object | None = None) -> None:
                self.log = log

            def get_file_nodes(self, file_key: str, node_ids: list[str]) -> dict[str, object]:
                return {
                    "name": "Checkout Flow",
                    "lastModified": "2026-05-01T12:00:00Z",
                    "version": "1234567890",
                    "editorType": "figma",
                    "nodes": {
                        "1:2": {
                            "document": {
                                "id": "1:2",
                                "name": "Checkout",
                                "type": "FRAME",
                                "children": [],
                            }
                        }
                    },
                }

            def get_local_variables(self, file_key: str) -> None:
                raise AssertionError("variables should not be fetched")

        with patch("figma_audit.ingestion.fetch_service.FigmaClient", FakeClient):
            bundle = fetch_figma_bundle(
                "https://www.figma.com/design/ABC123/File?node-id=1-2",
                fetch_variables=False,
            )

        normalized = normalize_figma_bundle(bundle)

        self.assertEqual(bundle.raw_file["name"], "Checkout Flow")
        self.assertEqual(bundle.raw_file["version"], "1234567890")
        self.assertEqual(normalized.file_name, "Checkout Flow")
        self.assertEqual(normalized.version, "1234567890")


class PipelineStatusTests(unittest.TestCase):
    def test_failed_fetch_stage_does_not_stay_running(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            status_path = root / "run_status.json"

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=RuntimeError("fetch stopped"),
            ):
                with self.assertRaisesRegex(RuntimeError, "fetch stopped"):
                    run_pipeline(
                        "https://www.figma.com/design/ABC123/File?node-id=1-2",
                        raw_output_path=root / "raw" / "raw_bundle.json",
                        normalized_output_path=root / "normalized" / "normalized_file.json",
                        audit_extraction_output_path=root / "extracted" / "audit_info.json",
                        audit_output_path=root / "normalized" / "audit_result.json",
                        detections_output_path=root / "detections" / "draft_detections.json",
                        audit_parts_output_dir=root / "parts",
                        annotations_output_dir=root / "annotations",
                        final_result_output_path=root / "final_result.json",
                        run_status_output_path=status_path,
                    )

            status = load_json(status_path)
            self.assertEqual(status["status"], "failed")
            self.assertFalse(any(stage["status"] == "running" for stage in status["stages"]))
            self.assertEqual(status["stages"][0]["name"], "fetch")
            self.assertEqual(status["stages"][0]["status"], "failed")
            self.assertIn("fetch stopped", status["stages"][0]["error"])

    def test_cache_first_uses_matching_raw_cache_without_live_fetch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            indexed_cache_path = root / "raw" / "cache" / "ABC123__node-1-2.json"
            status_path = root / "run_status.json"
            final_result_path = root / "final_result.json"
            cached_bundle = RawFigmaBundle(
                source_url="https://www.figma.com/design/ABC123/File?node-id=1-2",
                file_key="ABC123",
                node_id="1:2",
                raw_file={
                    "name": "Cached Node",
                    "document": {
                        "id": "1:2",
                        "name": "Cached Frame",
                        "type": "FRAME",
                        "absoluteBoundingBox": {
                            "x": 0,
                            "y": 0,
                            "width": 100,
                            "height": 50,
                        },
                        "children": [],
                    },
                },
            )
            save_json(indexed_cache_path, cached_bundle.model_dump(mode="json"))

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=AssertionError("live fetch should not run"),
            ):
                outputs = run_pipeline(
                    "https://www.figma.com/design/ABC123/File?node-id=1-2",
                    raw_output_path=raw_path,
                    normalized_output_path=root / "normalized" / "normalized_file.json",
                    audit_extraction_output_path=root / "extracted" / "audit_info.json",
                    audit_output_path=root / "normalized" / "audit_result.json",
                    detections_output_path=root / "detections" / "draft_detections.json",
                    audit_parts_output_dir=root / "parts",
                    annotations_output_dir=root / "annotations",
                    final_result_output_path=final_result_path,
                    run_status_output_path=status_path,
                    prefer_cached_fetch=True,
                    annotate_issues=False,
                    split_audit_output=False,
                )

            status = load_json(status_path)
            self.assertEqual(outputs.raw_bundle.file_key, "ABC123")
            self.assertTrue(status["stages"][0]["details"]["cache_used"])
            self.assertEqual(status["stages"][0]["details"]["cache_mode"], "cache_first")

    def test_cache_only_missing_cache_does_not_live_fetch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            status_path = root / "run_status.json"

            with patch("figma_audit.pipeline.validate_config") as validate_config, patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=AssertionError("live fetch should not run"),
            ) as fetch:
                with self.assertRaisesRegex(RuntimeError, "Cache-only mode requested") as ctx:
                    run_pipeline(
                        "https://www.figma.com/design/ABC123/File?node-id=0-1",
                        raw_output_path=raw_path,
                        normalized_output_path=root / "normalized" / "normalized_file.json",
                        audit_extraction_output_path=root / "extracted" / "audit_info.json",
                        audit_output_path=root / "normalized" / "audit_result.json",
                        detections_output_path=root / "detections" / "draft_detections.json",
                        audit_parts_output_dir=root / "parts",
                        annotations_output_dir=root / "annotations",
                        final_result_output_path=root / "final_result.json",
                        run_status_output_path=status_path,
                        cache_only_fetch=True,
                        annotate_issues=False,
                        split_audit_output=False,
                    )

            validate_config.assert_called_once_with(require_figma_token=False)
            fetch.assert_not_called()
            error_message = str(ctx.exception)
            self.assertIn("ABC123__node-0-1.json", error_message)
            self.assertIn("No live Figma API request was made", error_message)

            status = load_json(status_path)
            self.assertEqual(status["status"], "failed")
            self.assertIn("Cache-only mode requested", status["stages"][0]["error"])

    def test_cache_only_uses_matching_raw_cache_without_live_fetch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            indexed_cache_path = root / "raw" / "cache" / "ABC123__node-1-2.json"
            status_path = root / "run_status.json"
            final_result_path = root / "final_result.json"
            cached_bundle = RawFigmaBundle(
                source_url="https://www.figma.com/design/ABC123/File?node-id=1-2",
                file_key="ABC123",
                node_id="1:2",
                raw_file={
                    "name": "Cached Node",
                    "document": {
                        "id": "1:2",
                        "name": "Cached Frame",
                        "type": "FRAME",
                        "absoluteBoundingBox": {
                            "x": 0,
                            "y": 0,
                            "width": 100,
                            "height": 50,
                        },
                        "children": [],
                    },
                },
            )
            save_json(indexed_cache_path, cached_bundle.model_dump(mode="json"))

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=AssertionError("live fetch should not run"),
            ) as fetch:
                outputs = run_pipeline(
                    "https://www.figma.com/design/ABC123/File?node-id=1-2",
                    raw_output_path=raw_path,
                    normalized_output_path=root / "normalized" / "normalized_file.json",
                    audit_extraction_output_path=root / "extracted" / "audit_info.json",
                    audit_output_path=root / "normalized" / "audit_result.json",
                    detections_output_path=root / "detections" / "draft_detections.json",
                    audit_parts_output_dir=root / "parts",
                    annotations_output_dir=root / "annotations",
                    final_result_output_path=final_result_path,
                    run_status_output_path=status_path,
                    cache_only_fetch=True,
                    annotate_issues=False,
                    split_audit_output=False,
                )

            fetch.assert_not_called()
            status = load_json(status_path)
            self.assertEqual(outputs.raw_bundle.file_key, "ABC123")
            self.assertTrue(status["stages"][0]["details"]["cache_used"])
            self.assertEqual(status["stages"][0]["details"]["cache_mode"], "cache_only")
            self.assertTrue(
                any("Cache-only requested" in warning for warning in outputs.raw_bundle.warnings)
            )

    def test_rate_limited_missing_cache_explains_expected_cache_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            status_path = root / "run_status.json"
            rate_limit_error = FigmaRateLimitError(
                'Figma API rate limit for /v1/files/ABC123/nodes: {"status":429}',
                retry_after_seconds=106009,
                plan_tier="starter",
                rate_limit_type="high",
            )

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=rate_limit_error,
            ):
                with self.assertRaisesRegex(RuntimeError, "no matching raw cache exists") as ctx:
                    run_pipeline(
                        "https://www.figma.com/design/ABC123/File?node-id=0-1",
                        raw_output_path=raw_path,
                        normalized_output_path=root / "normalized" / "normalized_file.json",
                        audit_extraction_output_path=root / "extracted" / "audit_info.json",
                        audit_output_path=root / "normalized" / "audit_result.json",
                        detections_output_path=root / "detections" / "draft_detections.json",
                        audit_parts_output_dir=root / "parts",
                        annotations_output_dir=root / "annotations",
                        final_result_output_path=root / "final_result.json",
                        run_status_output_path=status_path,
                    )

            error_message = str(ctx.exception)
            self.assertIn("ABC123__node-0-1.json", error_message)
            self.assertIn("FIGMA_TOKEN", error_message)
            self.assertIn("wait for the Figma API quota to reset", error_message)
            self.assertIn("cannot safely reuse a cache from another Figma file", error_message)

            status = load_json(status_path)
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["stages"][0]["status"], "failed")
            self.assertIn("ABC123__node-0-1.json", status["stages"][0]["error"])

    def test_matching_cached_fetch_output_allows_pipeline_to_complete(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            status_path = root / "run_status.json"
            final_result_path = root / "final_result.json"
            cached_bundle = RawFigmaBundle(
                source_url="https://www.figma.com/design/ABC123/File?node-id=1-2",
                file_key="ABC123",
                node_id="1:2",
                raw_file={
                    "name": "Cached Node",
                    "document": {
                        "id": "1:2",
                        "name": "Cached Frame",
                        "type": "FRAME",
                        "absoluteBoundingBox": {
                            "x": 0,
                            "y": 0,
                            "width": 100,
                            "height": 50,
                        },
                        "children": [],
                    },
                },
            )
            save_json(raw_path, cached_bundle.model_dump(mode="json"))

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=RuntimeError("rate limited"),
            ):
                outputs = run_pipeline(
                    "https://www.figma.com/design/ABC123/File?node-id=1-2",
                    raw_output_path=raw_path,
                    normalized_output_path=root / "normalized" / "normalized_file.json",
                    audit_extraction_output_path=root / "extracted" / "audit_info.json",
                    audit_output_path=root / "normalized" / "audit_result.json",
                    detections_output_path=root / "detections" / "draft_detections.json",
                    audit_parts_output_dir=root / "parts",
                    annotations_output_dir=root / "annotations",
                    final_result_output_path=final_result_path,
                    run_status_output_path=status_path,
                    annotate_issues=False,
                    split_audit_output=False,
                )

            status = load_json(status_path)
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["stages"][0]["status"], "completed")
            self.assertTrue(status["stages"][0]["details"]["cache_used"])
            self.assertTrue(final_result_path.exists())
            self.assertEqual(outputs.normalized_file.file_key, "ABC123")
            self.assertTrue(
                any("reused cached raw output" in warning for warning in outputs.raw_bundle.warnings)
            )

    def test_indexed_raw_cache_allows_reuse_after_another_file_was_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw" / "raw_bundle.json"
            indexed_cache_path = root / "raw" / "cache" / "ABC123__node-1-2.json"
            status_path = root / "run_status.json"
            final_result_path = root / "final_result.json"

            other_bundle = RawFigmaBundle(
                source_url="https://www.figma.com/design/OTHER/File?node-id=9-9",
                file_key="OTHER",
                node_id="9:9",
                raw_file={
                    "name": "Other",
                    "document": {"id": "9:9", "name": "Other", "type": "FRAME", "children": []},
                },
            )
            cached_bundle = RawFigmaBundle(
                source_url="https://www.figma.com/design/ABC123/File?node-id=1-2",
                file_key="ABC123",
                node_id="1:2",
                raw_file={
                    "name": "Cached Node",
                    "document": {
                        "id": "1:2",
                        "name": "Cached Frame",
                        "type": "FRAME",
                        "absoluteBoundingBox": {
                            "x": 0,
                            "y": 0,
                            "width": 100,
                            "height": 50,
                        },
                        "children": [],
                    },
                },
            )
            save_json(raw_path, other_bundle.model_dump(mode="json"))
            save_json(indexed_cache_path, cached_bundle.model_dump(mode="json"))

            with patch("figma_audit.pipeline.validate_config"), patch(
                "figma_audit.pipeline.fetch_figma_bundle",
                side_effect=RuntimeError("rate limited"),
            ):
                outputs = run_pipeline(
                    "https://www.figma.com/design/ABC123/File?node-id=1-2",
                    raw_output_path=raw_path,
                    normalized_output_path=root / "normalized" / "normalized_file.json",
                    audit_extraction_output_path=root / "extracted" / "audit_info.json",
                    audit_output_path=root / "normalized" / "audit_result.json",
                    detections_output_path=root / "detections" / "draft_detections.json",
                    audit_parts_output_dir=root / "parts",
                    annotations_output_dir=root / "annotations",
                    final_result_output_path=final_result_path,
                    run_status_output_path=status_path,
                    annotate_issues=False,
                    split_audit_output=False,
                )

            self.assertEqual(outputs.raw_bundle.file_key, "ABC123")
            self.assertEqual(outputs.raw_bundle.node_id, "1:2")
            self.assertEqual(load_json(raw_path)["file_key"], "ABC123")


class NormalizerTests(unittest.TestCase):
    def test_node_based_fetch_keeps_root_node_for_annotation_context(self) -> None:
        bundle = RawFigmaBundle(
            source_url="https://www.figma.com/design/ABC/File?node-id=1-2",
            file_key="ABC",
            node_id="1:2",
            raw_file={
                "name": "Node 1:2",
                "document": {
                    "id": "1:2",
                    "name": "Selected Frame",
                    "type": "FRAME",
                    "absoluteBoundingBox": {
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                    "children": [],
                },
            },
        )

        normalized = normalize_figma_bundle(bundle)

        self.assertEqual([node.id for node in normalized.nodes], ["1:2"])
        self.assertEqual([frame.id for frame in normalized.frames], ["1:2"])


class DetectionTests(unittest.TestCase):
    def test_low_contrast_text_sets_binary_trust_accessibility_status(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="frame:1",
                    name="Frame",
                    type="FRAME",
                    path="Frame",
                    depth=0,
                    fills=[
                        {
                            "type": "SOLID",
                            "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                            "opacity": 1,
                        }
                    ],
                    absolute_bounding_box={
                        "x": 0,
                        "y": 0,
                        "width": 200,
                        "height": 100,
                    },
                ),
                NormalizedNode(
                    id="text:1",
                    name="Body text",
                    type="TEXT",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > Body text",
                    depth=1,
                    characters="Low contrast text",
                    style={"fontSize": 14, "fontWeight": 400},
                    text_style={"fontSize": 14, "fontWeight": 400},
                    fills=[
                        {
                            "type": "SOLID",
                            "color": {"r": 0.7, "g": 0.7, "b": 0.7, "a": 1},
                            "opacity": 1,
                        }
                    ],
                    absolute_bounding_box={
                        "x": 10,
                        "y": 10,
                        "width": 120,
                        "height": 20,
                    },
                ),
            ],
        )

        detection_result = run_detections(normalized_file)
        statuses = detection_result.status_by_id()

        self.assertTrue(statuses["trust_accessibility"].exists)
        self.assertEqual(statuses["trust_accessibility"].issue_count, 1)
        self.assertEqual(statuses["task_execution"].exists, False)
        self.assertEqual(detection_result.summary.draft_issue_count, 1)
        self.assertEqual(detection_result.draft_issues[0].criterion, "trust_accessibility")
        self.assertEqual(detection_result.draft_issues[0].evidence["confidence"], "high")
        self.assertEqual(
            detection_result.draft_issues[0].evidence["validation_method"],
            "mobile_accessibility_scanner_static_figma_gate",
        )
        self.assertEqual(detection_result.draft_issues[0].evidence["accessibility_check"], "text_contrast")

    def test_trust_accessibility_flags_small_visible_touch_target(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Settings screen",
                    type="FRAME",
                    path="Settings screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:close",
                    name="Close button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Settings screen > Close button",
                    depth=1,
                    absolute_bounding_box={"x": 348, "y": 28, "width": 28, "height": 28},
                ),
                NormalizedNode(
                    id="icon:close",
                    name="Close icon",
                    type="VECTOR",
                    parent_id="button:close",
                    path="Settings screen > Close button > Close icon",
                    depth=2,
                    absolute_bounding_box={"x": 354, "y": 34, "width": 16, "height": 16},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        trust_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "trust_accessibility"
        ]

        self.assertEqual(len(trust_issues), 1)
        self.assertEqual(trust_issues[0].evidence["detector_id"], "small_touch_target")
        self.assertEqual(trust_issues[0].evidence["accessibility_check"], "touch_target_size")
        self.assertEqual(trust_issues[0].evidence["smallest_side"], 28)

    def test_trust_accessibility_accepts_small_icon_inside_adequate_touch_target(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Settings screen",
                    type="FRAME",
                    path="Settings screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:close",
                    name="Close button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Settings screen > Close button",
                    depth=1,
                    absolute_bounding_box={"x": 328, "y": 20, "width": 48, "height": 48},
                ),
                NormalizedNode(
                    id="icon:close",
                    name="Close icon",
                    type="VECTOR",
                    parent_id="button:close",
                    path="Settings screen > Close button > Close icon",
                    depth=2,
                    absolute_bounding_box={"x": 342, "y": 34, "width": 20, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["trust_accessibility"].exists)

    def test_trust_accessibility_flags_small_important_text(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Product screen",
                    type="FRAME",
                    path="Product screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:price",
                    name="Price label",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Product screen > Price label",
                    depth=1,
                    characters="$26.99",
                    text_style={"fontSize": 8, "fontWeight": 600},
                    absolute_bounding_box={"x": 24, "y": 120, "width": 42, "height": 10},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        trust_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "trust_accessibility"
        ]

        self.assertTrue(
            any(issue.evidence["detector_id"] == "small_text_readability" for issue in trust_issues)
        )
        self.assertTrue(
            all(issue.evidence.get("criterion_name") for issue in trust_issues)
        )

    def test_trust_accessibility_flags_crowded_touch_targets(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Toolbar screen",
                    type="FRAME",
                    path="Toolbar screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:add",
                    name="Add button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Toolbar screen > Add button",
                    depth=1,
                    absolute_bounding_box={"x": 300, "y": 24, "width": 40, "height": 40},
                ),
                NormalizedNode(
                    id="button:filter",
                    name="Filter button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Toolbar screen > Filter button",
                    depth=1,
                    absolute_bounding_box={"x": 343, "y": 24, "width": 40, "height": 40},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        trust_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "trust_accessibility"
        ]

        self.assertTrue(
            any(issue.evidence["detector_id"] == "crowded_touch_target" for issue in trust_issues)
        )

    def test_trust_accessibility_flags_ambiguous_icon_only_control(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Toolbar screen",
                    type="FRAME",
                    path="Toolbar screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:icon",
                    name="Icon button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Toolbar screen > Icon button",
                    depth=1,
                    absolute_bounding_box={"x": 320, "y": 24, "width": 44, "height": 44},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        trust_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "trust_accessibility"
        ]

        self.assertTrue(
            any(issue.evidence["detector_id"] == "icon_only_unlabeled_control" for issue in trust_issues)
        )

    def test_trust_accessibility_ignores_small_decorative_icon(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Profile screen",
                    type="FRAME",
                    path="Profile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="icon:decorative",
                    name="Sparkle icon",
                    type="VECTOR",
                    parent_id="screen:1",
                    path="Profile screen > Sparkle icon",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 16, "height": 16},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["trust_accessibility"].exists)

    def test_task_execution_flags_multifield_form_without_completion_action(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Sign in screen",
                    type="FRAME",
                    path="Sign in screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="field:email",
                    name="Email input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Sign in screen > Email input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:email",
                    name="Label",
                    type="TEXT",
                    parent_id="field:email",
                    path="Sign in screen > Email input field > Label",
                    depth=2,
                    characters="Email",
                    absolute_bounding_box={"x": 40, "y": 136, "width": 80, "height": 20},
                ),
                NormalizedNode(
                    id="field:password",
                    name="Password input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Sign in screen > Password input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 188, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:password",
                    name="Label",
                    type="TEXT",
                    parent_id="field:password",
                    path="Sign in screen > Password input field > Label",
                    depth=2,
                    characters="Password",
                    absolute_bounding_box={"x": 40, "y": 204, "width": 110, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        task_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "task_execution"
        ]

        self.assertEqual(len(task_issues), 1)
        self.assertEqual(task_issues[0].evidence["detector_id"], "form_without_completion_action")
        self.assertEqual(task_issues[0].evidence["field_purposes"], ["email", "password"])
        self.assertEqual(
            task_issues[0].evidence["validation_method"],
            "cognitive_walkthrough_static_figma_gate",
        )

    def test_task_execution_does_not_flag_multifield_form_with_completion_action(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Sign in screen",
                    type="FRAME",
                    path="Sign in screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="field:email",
                    name="Email input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Sign in screen > Email input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:email",
                    name="Label",
                    type="TEXT",
                    parent_id="field:email",
                    path="Sign in screen > Email input field > Label",
                    depth=2,
                    characters="Email",
                    absolute_bounding_box={"x": 40, "y": 136, "width": 80, "height": 20},
                ),
                NormalizedNode(
                    id="field:password",
                    name="Password input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Sign in screen > Password input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 188, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:password",
                    name="Label",
                    type="TEXT",
                    parent_id="field:password",
                    path="Sign in screen > Password input field > Label",
                    depth=2,
                    characters="Password",
                    absolute_bounding_box={"x": 40, "y": 204, "width": 110, "height": 20},
                ),
                NormalizedNode(
                    id="button:signin",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Sign in screen > Primary button",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 268, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:signin",
                    name="Button label",
                    type="TEXT",
                    parent_id="button:signin",
                    path="Sign in screen > Primary button > Button label",
                    depth=2,
                    characters="Sign in",
                    absolute_bounding_box={"x": 160, "y": 284, "width": 80, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["task_execution"].exists)

    def test_task_execution_flags_generic_completion_action_without_task_context(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Mobile screen",
                    type="FRAME",
                    path="Mobile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="field:email",
                    name="Email input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Mobile screen > Email input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:email",
                    name="Label",
                    type="TEXT",
                    parent_id="field:email",
                    path="Mobile screen > Email input field > Label",
                    depth=2,
                    characters="Email",
                    absolute_bounding_box={"x": 40, "y": 136, "width": 80, "height": 20},
                ),
                NormalizedNode(
                    id="field:password",
                    name="Password input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Mobile screen > Password input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 188, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:password",
                    name="Label",
                    type="TEXT",
                    parent_id="field:password",
                    path="Mobile screen > Password input field > Label",
                    depth=2,
                    characters="Password",
                    absolute_bounding_box={"x": 40, "y": 204, "width": 110, "height": 20},
                ),
                NormalizedNode(
                    id="button:continue",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Mobile screen > Primary button",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 268, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:continue",
                    name="Button label",
                    type="TEXT",
                    parent_id="button:continue",
                    path="Mobile screen > Primary button > Button label",
                    depth=2,
                    characters="Continue",
                    absolute_bounding_box={"x": 160, "y": 284, "width": 86, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        task_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "task_execution"
        ]

        self.assertEqual(len(task_issues), 1)
        self.assertEqual(task_issues[0].evidence["detector_id"], "ambiguous_completion_action")
        self.assertEqual(task_issues[0].evidence["ambiguous_action_label"], "continue")
        self.assertIn(
            "cognitive_walkthrough_question",
            task_issues[0].evidence,
        )

    def test_task_execution_accepts_generic_completion_action_with_task_context(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Create account screen",
                    type="FRAME",
                    path="Create account screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="field:email",
                    name="Email input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Create account screen > Email input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:email",
                    name="Label",
                    type="TEXT",
                    parent_id="field:email",
                    path="Create account screen > Email input field > Label",
                    depth=2,
                    characters="Email",
                    absolute_bounding_box={"x": 40, "y": 136, "width": 80, "height": 20},
                ),
                NormalizedNode(
                    id="field:password",
                    name="Password input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Create account screen > Password input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 188, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:password",
                    name="Label",
                    type="TEXT",
                    parent_id="field:password",
                    path="Create account screen > Password input field > Label",
                    depth=2,
                    characters="Password",
                    absolute_bounding_box={"x": 40, "y": 204, "width": 110, "height": 20},
                ),
                NormalizedNode(
                    id="button:continue",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Create account screen > Primary button",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 268, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:continue",
                    name="Button label",
                    type="TEXT",
                    parent_id="button:continue",
                    path="Create account screen > Primary button > Button label",
                    depth=2,
                    characters="Continue",
                    absolute_bounding_box={"x": 160, "y": 284, "width": 86, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["task_execution"].exists)

    def test_task_execution_does_not_treat_plain_instruction_text_as_completion_action(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Mobile screen",
                    type="FRAME",
                    path="Mobile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="field:email",
                    name="Email input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Mobile screen > Email input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:email",
                    name="Label",
                    type="TEXT",
                    parent_id="field:email",
                    path="Mobile screen > Email input field > Label",
                    depth=2,
                    characters="Email",
                    absolute_bounding_box={"x": 40, "y": 136, "width": 80, "height": 20},
                ),
                NormalizedNode(
                    id="field:password",
                    name="Password input field",
                    type="INSTANCE",
                    parent_id="screen:1",
                    path="Mobile screen > Password input field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 188, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:password",
                    name="Label",
                    type="TEXT",
                    parent_id="field:password",
                    path="Mobile screen > Password input field > Label",
                    depth=2,
                    characters="Password",
                    absolute_bounding_box={"x": 40, "y": 204, "width": 110, "height": 20},
                ),
                NormalizedNode(
                    id="text:instruction",
                    name="Instruction",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Mobile screen > Instruction",
                    depth=1,
                    characters="Continue",
                    absolute_bounding_box={"x": 24, "y": 268, "width": 86, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        task_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "task_execution"
        ]

        self.assertEqual(len(task_issues), 1)
        self.assertEqual(task_issues[0].evidence["detector_id"], "form_without_completion_action")

    def test_task_execution_respects_cancel_in_destructive_action_sheet(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Profile screen",
                    type="FRAME",
                    path="Profile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="sheet:1",
                    name="ActionSheet",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Profile screen > ActionSheet",
                    depth=1,
                    absolute_bounding_box={"x": 20, "y": 520, "width": 353, "height": 180},
                ),
                NormalizedNode(
                    id="row:delete",
                    name="Delete action",
                    type="FRAME",
                    parent_id="sheet:1",
                    path="Profile screen > ActionSheet > Delete action",
                    depth=2,
                    absolute_bounding_box={"x": 20, "y": 520, "width": 353, "height": 56},
                ),
                NormalizedNode(
                    id="text:delete",
                    name="Action label",
                    type="TEXT",
                    parent_id="row:delete",
                    path="Profile screen > ActionSheet > Delete action > Action label",
                    depth=3,
                    characters="Delete",
                    absolute_bounding_box={"x": 160, "y": 538, "width": 70, "height": 20},
                ),
                NormalizedNode(
                    id="row:cancel",
                    name="Cancel action",
                    type="FRAME",
                    parent_id="sheet:1",
                    path="Profile screen > ActionSheet > Cancel action",
                    depth=2,
                    absolute_bounding_box={"x": 20, "y": 632, "width": 353, "height": 56},
                ),
                NormalizedNode(
                    id="text:cancel",
                    name="Action label",
                    type="TEXT",
                    parent_id="row:cancel",
                    path="Profile screen > ActionSheet > Cancel action > Action label",
                    depth=3,
                    characters="Cancel",
                    absolute_bounding_box={"x": 160, "y": 650, "width": 70, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["task_execution"].exists)

    def test_task_execution_flags_destructive_action_without_recovery(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Profile screen",
                    type="FRAME",
                    path="Profile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="sheet:1",
                    name="ActionSheet",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Profile screen > ActionSheet",
                    depth=1,
                    absolute_bounding_box={"x": 20, "y": 520, "width": 353, "height": 100},
                ),
                NormalizedNode(
                    id="row:delete",
                    name="Delete action",
                    type="FRAME",
                    parent_id="sheet:1",
                    path="Profile screen > ActionSheet > Delete action",
                    depth=2,
                    absolute_bounding_box={"x": 20, "y": 520, "width": 353, "height": 56},
                ),
                NormalizedNode(
                    id="text:delete",
                    name="Action label",
                    type="TEXT",
                    parent_id="row:delete",
                    path="Profile screen > ActionSheet > Delete action > Action label",
                    depth=3,
                    characters="Delete",
                    absolute_bounding_box={"x": 160, "y": 538, "width": 70, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        task_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "task_execution"
        ]

        self.assertEqual(len(task_issues), 1)
        self.assertEqual(task_issues[0].evidence["detector_id"], "destructive_action_without_recovery")
        self.assertEqual(
            task_issues[0].evidence["validation_method"],
            "cognitive_walkthrough_static_figma_gate",
        )

    def test_flow_architecture_flags_repeated_generic_navigation_labels(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Home screen",
                    type="FRAME",
                    path="Home screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="nav:1",
                    name="Bottom Tab Bar",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Home screen > Bottom Tab Bar",
                    depth=1,
                    layout_mode="HORIZONTAL",
                    absolute_bounding_box={"x": 0, "y": 728, "width": 393, "height": 84},
                ),
                NormalizedNode(
                    id="tab:1",
                    name="Tab item",
                    type="FRAME",
                    parent_id="nav:1",
                    path="Home screen > Bottom Tab Bar > Tab item",
                    depth=2,
                    absolute_bounding_box={"x": 0, "y": 728, "width": 131, "height": 84},
                ),
                NormalizedNode(
                    id="text:label1",
                    name="Tab label",
                    type="TEXT",
                    parent_id="tab:1",
                    path="Home screen > Bottom Tab Bar > Tab item > Tab label",
                    depth=3,
                    characters="Label",
                    absolute_bounding_box={"x": 40, "y": 776, "width": 44, "height": 18},
                ),
                NormalizedNode(
                    id="tab:2",
                    name="Tab item",
                    type="FRAME",
                    parent_id="nav:1",
                    path="Home screen > Bottom Tab Bar > Tab item",
                    depth=2,
                    absolute_bounding_box={"x": 131, "y": 728, "width": 131, "height": 84},
                ),
                NormalizedNode(
                    id="text:label2",
                    name="Tab label",
                    type="TEXT",
                    parent_id="tab:2",
                    path="Home screen > Bottom Tab Bar > Tab item > Tab label",
                    depth=3,
                    characters="Label",
                    absolute_bounding_box={"x": 171, "y": 776, "width": 44, "height": 18},
                ),
                NormalizedNode(
                    id="tab:3",
                    name="Tab item",
                    type="FRAME",
                    parent_id="nav:1",
                    path="Home screen > Bottom Tab Bar > Tab item",
                    depth=2,
                    absolute_bounding_box={"x": 262, "y": 728, "width": 131, "height": 84},
                ),
                NormalizedNode(
                    id="text:label3",
                    name="Tab label",
                    type="TEXT",
                    parent_id="tab:3",
                    path="Home screen > Bottom Tab Bar > Tab item > Tab label",
                    depth=3,
                    characters="Label",
                    absolute_bounding_box={"x": 302, "y": 776, "width": 44, "height": 18},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        flow_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "flow_architecture"
        ]

        self.assertEqual(len(flow_issues), 1)
        self.assertEqual(flow_issues[0].evidence["detector_id"], "generic_navigation_label")
        self.assertEqual(
            flow_issues[0].evidence["validation_method"],
            "visual_search_coding_static_figma_gate",
        )
        self.assertEqual(
            flow_issues[0].evidence["flow_subdetector"],
            "generic_or_repeated_destination_labels",
        )
        self.assertEqual(flow_issues[0].evidence["duplicated_labels"], {"label": 3})
        self.assertTrue(flow_issues[0].evidence["visual_search_checks"]["layout_regular"])
        self.assertEqual(flow_issues[0].location.node_id, "text:label1")
        self.assertEqual(flow_issues[0].evidence["navigation_ancestor_id"], "nav:1")

    def test_flow_architecture_climbs_from_tab_item_to_whole_tab_bar(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Example screen",
                type="FRAME",
                path="Example screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="tabbar:1",
                name="TabBar",
                type="INSTANCE",
                parent_id="screen:1",
                path="Example screen > TabBar",
                depth=1,
                layout_mode="VERTICAL",
                absolute_bounding_box={"x": 0, "y": 728, "width": 393, "height": 84},
            ),
            NormalizedNode(
                id="tabs:1",
                name="Tabs",
                type="FRAME",
                parent_id="tabbar:1",
                path="Example screen > TabBar > Tabs",
                depth=2,
                absolute_bounding_box={"x": 0, "y": 728, "width": 393, "height": 50},
            ),
        ]
        for index in range(5):
            tab_id = f"tab:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=tab_id,
                        name="_TabBar-tab",
                        type="INSTANCE",
                        parent_id="tabs:1",
                        path="Example screen > TabBar > Tabs > _TabBar-tab",
                        depth=3,
                        absolute_bounding_box={"x": 1 + 79 * index, "y": 728, "width": 75, "height": 50},
                    ),
                    NormalizedNode(
                        id=f"text:label:{index}",
                        name="Tab label",
                        type="TEXT",
                        parent_id=tab_id,
                        path="Example screen > TabBar > Tabs > _TabBar-tab > Tab label",
                        depth=4,
                        characters="Label",
                        absolute_bounding_box={"x": 20 + 79 * index, "y": 760, "width": 44, "height": 18},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        flow_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "flow_architecture"
        ]

        self.assertEqual(len(flow_issues), 1)
        self.assertIn(flow_issues[0].evidence["navigation_ancestor_name"], {"Tabs", "TabBar"})
        self.assertEqual(flow_issues[0].evidence["duplicated_labels"], {"label": 5})
        self.assertTrue(str(flow_issues[0].location.node_id).startswith("text:label:"))
        self.assertIn(flow_issues[0].evidence["navigation_ancestor_id"], {"tabs:1", "tabbar:1"})

    def test_flow_architecture_accepts_specific_navigation_labels(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Home screen",
                type="FRAME",
                path="Home screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="nav:1",
                name="Bottom Navigation",
                type="FRAME",
                parent_id="screen:1",
                path="Home screen > Bottom Navigation",
                depth=1,
                layout_mode="HORIZONTAL",
                absolute_bounding_box={"x": 0, "y": 728, "width": 393, "height": 84},
            ),
        ]
        for index, label in enumerate(["Home", "Search", "Profile"]):
            tab_id = f"tab:{index}"
            nodes.append(
                NormalizedNode(
                    id=tab_id,
                    name="Tab item",
                    type="FRAME",
                    parent_id="nav:1",
                    path=f"Home screen > Bottom Navigation > {label}",
                    depth=2,
                    absolute_bounding_box={"x": 131 * index, "y": 728, "width": 131, "height": 84},
                )
            )
            nodes.append(
                NormalizedNode(
                    id=f"text:{index}",
                    name="Tab label",
                    type="TEXT",
                    parent_id=tab_id,
                    path=f"Home screen > Bottom Navigation > {label} > Tab label",
                    depth=3,
                    characters=label,
                    absolute_bounding_box={"x": 20 + 131 * index, "y": 776, "width": 80, "height": 18},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["flow_architecture"].exists)

    def test_flow_architecture_flags_icon_only_primary_navigation_without_destination_coding(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Home screen",
                type="FRAME",
                path="Home screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="nav:1",
                name="Bottom Navigation",
                type="FRAME",
                parent_id="screen:1",
                path="Home screen > Bottom Navigation",
                depth=1,
                layout_mode="HORIZONTAL",
                absolute_bounding_box={"x": 0, "y": 728, "width": 393, "height": 84},
            ),
        ]
        for index in range(3):
            tab_id = f"tab:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=tab_id,
                        name="Tab item",
                        type="FRAME",
                        parent_id="nav:1",
                        path="Home screen > Bottom Navigation > Tab item",
                        depth=2,
                        absolute_bounding_box={"x": 131 * index, "y": 728, "width": 131, "height": 84},
                    ),
                    NormalizedNode(
                        id=f"icon:{index}",
                        name="Icon",
                        type="VECTOR",
                        parent_id=tab_id,
                        path="Home screen > Bottom Navigation > Tab item > Icon",
                        depth=3,
                        absolute_bounding_box={"x": 54 + 131 * index, "y": 752, "width": 24, "height": 24},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        flow_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "flow_architecture"
        ]

        self.assertEqual(len(flow_issues), 1)
        self.assertEqual(flow_issues[0].evidence["detector_id"], "generic_navigation_label")
        self.assertEqual(
            flow_issues[0].evidence["flow_subdetector"],
            "missing_destination_labels_in_primary_navigation",
        )
        self.assertEqual(flow_issues[0].evidence["visual_search_checks"]["unlabeled_item_count"], 3)
        self.assertEqual(flow_issues[0].location.node_id, "nav:1")

    def test_flow_architecture_ignores_icon_only_toolbar_outside_primary_navigation(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Editor screen",
                type="FRAME",
                path="Editor screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="toolbar:1",
                name="Toolbar",
                type="FRAME",
                parent_id="screen:1",
                path="Editor screen > Toolbar",
                depth=1,
                layout_mode="HORIZONTAL",
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 56},
            ),
        ]
        for index in range(3):
            button_id = f"button:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=button_id,
                        name="Toolbar button",
                        type="FRAME",
                        parent_id="toolbar:1",
                        path="Editor screen > Toolbar > Toolbar button",
                        depth=2,
                        absolute_bounding_box={"x": 12 + 44 * index, "y": 6, "width": 44, "height": 44},
                    ),
                    NormalizedNode(
                        id=f"icon:{index}",
                        name="Icon",
                        type="VECTOR",
                        parent_id=button_id,
                        path="Editor screen > Toolbar > Toolbar button > Icon",
                        depth=3,
                        absolute_bounding_box={"x": 22 + 44 * index, "y": 16, "width": 24, "height": 24},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["flow_architecture"].exists)

    def test_flow_architecture_ignores_generic_label_outside_navigation(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Home screen",
                    type="FRAME",
                    path="Home screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="card:1",
                    name="Content card",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Home screen > Content card",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 120, "width": 345, "height": 160},
                ),
                NormalizedNode(
                    id="text:label",
                    name="Caption",
                    type="TEXT",
                    parent_id="card:1",
                    path="Home screen > Content card > Caption",
                    depth=2,
                    characters="Label",
                    absolute_bounding_box={"x": 40, "y": 144, "width": 60, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["flow_architecture"].exists)

    def test_flow_architecture_ignores_repeated_content_card_headings(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Whats New screen",
                type="FRAME",
                path="Whats New screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="list:1",
                name="What's New Items",
                type="FRAME",
                parent_id="screen:1",
                path="Whats New screen > What's New Items",
                depth=1,
                layout_mode="VERTICAL",
                absolute_bounding_box={"x": 32, "y": 160, "width": 329, "height": 108},
            ),
        ]
        for index in range(3):
            item_id = f"item:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=item_id,
                        name="WhatsNew item",
                        type="FRAME",
                        parent_id="list:1",
                        path="Whats New screen > What's New Items > Item",
                        depth=2,
                        absolute_bounding_box={"x": 32, "y": 160 + index * 36, "width": 329, "height": 32},
                    ),
                    NormalizedNode(
                        id=f"icon:{index}",
                        name="Icon",
                        type="VECTOR",
                        parent_id=item_id,
                        path="Whats New screen > What's New Items > Item > Icon",
                        depth=3,
                        absolute_bounding_box={"x": 40, "y": 166 + index * 36, "width": 20, "height": 20},
                    ),
                    NormalizedNode(
                        id=f"heading:{index}",
                        name="Heading",
                        type="TEXT",
                        parent_id=item_id,
                        path="Whats New screen > What's New Items > Item > Heading",
                        depth=3,
                        characters="Heading",
                        absolute_bounding_box={"x": 72, "y": 160 + index * 36, "width": 100, "height": 16},
                    ),
                    NormalizedNode(
                        id=f"subheading:{index}",
                        name="Subheading",
                        type="TEXT",
                        parent_id=item_id,
                        path="Whats New screen > What's New Items > Item > Subheading",
                        depth=3,
                        characters="Subheading",
                        absolute_bounding_box={"x": 72, "y": 178 + index * 36, "width": 140, "height": 14},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["flow_architecture"].exists)

    def test_flow_architecture_ignores_repeated_app_grid_labels(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="App library screen",
                type="FRAME",
                path="App library screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="grid:1",
                name="Suggested Apps Grid",
                type="FRAME",
                parent_id="screen:1",
                path="App library screen > Suggested Apps Grid",
                depth=1,
                layout_mode="HORIZONTAL",
                absolute_bounding_box={"x": 16, "y": 604, "width": 361, "height": 120},
            ),
        ]
        for index in range(4):
            item_id = f"app:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=item_id,
                        name="App card",
                        type="FRAME",
                        parent_id="grid:1",
                        path="App library screen > Suggested Apps Grid > App card",
                        depth=2,
                        absolute_bounding_box={"x": 16 + 88 * index, "y": 604, "width": 80, "height": 120},
                    ),
                    NormalizedNode(
                        id=f"icon:{index}",
                        name="App icon",
                        type="VECTOR",
                        parent_id=item_id,
                        path="App library screen > Suggested Apps Grid > App card > App icon",
                        depth=3,
                        absolute_bounding_box={"x": 36 + 88 * index, "y": 616, "width": 40, "height": 40},
                    ),
                    NormalizedNode(
                        id=f"text:app:{index}",
                        name="App name",
                        type="TEXT",
                        parent_id=item_id,
                        path="App library screen > Suggested Apps Grid > App card > App name",
                        depth=3,
                        characters="App Name",
                        absolute_bounding_box={"x": 22 + 88 * index, "y": 664, "width": 68, "height": 18},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["flow_architecture"].exists)

    def test_ui_consistency_flags_repeated_control_style_outlier_with_inspection_evidence(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Checkout screen",
                type="FRAME",
                path="Checkout screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            )
        ]
        for index, radius in enumerate([8, 8, 8, 24]):
            nodes.append(
                NormalizedNode(
                    id=f"button:{index}",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Checkout screen > Primary button",
                    depth=1,
                    corner_radius=radius,
                    padding_left=16,
                    padding_right=16,
                    absolute_bounding_box={"x": 24, "y": 120 + index * 64, "width": 160, "height": 48},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        ui_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "ui_consistency"
        ]

        self.assertEqual(len(ui_issues), 1)
        self.assertEqual(ui_issues[0].evidence["detector_id"], "component_style_outlier")
        self.assertEqual(
            ui_issues[0].evidence["validation_method"],
            "consistency_inspection_static_figma_gate",
        )
        self.assertEqual(ui_issues[0].evidence["field"], "corner_radius")
        self.assertEqual(ui_issues[0].evidence["field_dimension"], "perceptual")
        self.assertEqual(ui_issues[0].location.node_id, "button:3")

    def test_ui_consistency_accepts_documented_state_variant(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Filters screen",
                type="FRAME",
                path="Filters screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            )
        ]
        for index, name in enumerate(["Filter chip", "Filter chip", "Filter chip", "Selected filter chip"]):
            nodes.append(
                NormalizedNode(
                    id=f"chip:{index}",
                    name=name,
                    type="FRAME",
                    parent_id="screen:1",
                    path=f"Filters screen > {name}",
                    depth=1,
                    corner_radius=16 if "Selected" in name else 8,
                    absolute_bounding_box={"x": 24 + index * 82, "y": 120, "width": 72, "height": 36},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["ui_consistency"].exists)

    def test_ui_consistency_flags_lexical_outlier_in_repeated_action_family(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Editor screen",
                type="FRAME",
                path="Editor screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            )
        ]
        for index, label in enumerate(["Save", "Save", "Save", "Done"]):
            button_id = f"button:{index}"
            nodes.extend(
                [
                    NormalizedNode(
                        id=button_id,
                        name="Primary button",
                        type="FRAME",
                        parent_id="screen:1",
                        path="Editor screen > Primary button",
                        depth=1,
                        absolute_bounding_box={"x": 24, "y": 120 + index * 64, "width": 160, "height": 48},
                    ),
                    NormalizedNode(
                        id=f"text:{index}",
                        name="Button label",
                        type="TEXT",
                        parent_id=button_id,
                        path="Editor screen > Primary button > Button label",
                        depth=2,
                        characters=label,
                        absolute_bounding_box={"x": 72, "y": 134 + index * 64, "width": 60, "height": 20},
                    ),
                ]
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        ui_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "ui_consistency"
        ]

        self.assertEqual(len(ui_issues), 1)
        self.assertEqual(ui_issues[0].evidence["ui_consistency_subdetector"], "lexical_label_outlier")
        self.assertEqual(ui_issues[0].evidence["dominant_label"], "save")
        self.assertEqual(ui_issues[0].evidence["outlier_label"], "done")
        self.assertEqual(ui_issues[0].evidence["consistency_dimensions"], ["lexical"])

    def test_visual_brand_flags_flat_text_hierarchy_with_balanced_aesthetic_evidence(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Landing screen",
                type="FRAME",
                path="Landing screen",
                depth=0,
                fills=[
                    {
                        "type": "SOLID",
                        "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                        "opacity": 1,
                    }
                ],
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            )
        ]
        for index in range(5):
            nodes.append(
                NormalizedNode(
                    id=f"text:{index}",
                    name="Body text",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Landing screen > Body text",
                    depth=1,
                    characters=f"Message {index}",
                    text_style={"fontSize": 14, "fontWeight": 400},
                    fills=[
                        {
                            "type": "SOLID",
                            "color": {"r": 0, "g": 0, "b": 0, "a": 1},
                            "opacity": 1,
                        }
                    ],
                    absolute_bounding_box={"x": 24, "y": 80 + index * 36, "width": 180, "height": 20},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        visual_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "visual_brand"
        ]

        self.assertEqual(len(visual_issues), 1)
        self.assertEqual(visual_issues[0].evidence["detector_id"], "flat_visual_hierarchy")
        self.assertEqual(
            visual_issues[0].evidence["validation_method"],
            "balanced_aesthetics_static_figma_gate",
        )
        self.assertEqual(visual_issues[0].evidence["visual_brand_subdetector"], "flat_text_hierarchy")
        self.assertEqual(visual_issues[0].evidence["largest_to_median_ratio"], 1.0)

    def test_visual_brand_accepts_clear_text_hierarchy(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Landing screen",
                type="FRAME",
                path="Landing screen",
                depth=0,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="heading:1",
                name="Heading",
                type="TEXT",
                parent_id="screen:1",
                path="Landing screen > Heading",
                depth=1,
                characters="A clear product headline",
                text_style={"fontSize": 34, "fontWeight": 700},
                absolute_bounding_box={"x": 24, "y": 80, "width": 300, "height": 44},
            ),
        ]
        for index in range(5):
            nodes.append(
                NormalizedNode(
                    id=f"body:{index}",
                    name="Body text",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Landing screen > Body text",
                    depth=1,
                    characters=f"Body {index}",
                    text_style={"fontSize": 14, "fontWeight": 400},
                    absolute_bounding_box={"x": 24, "y": 150 + index * 32, "width": 220, "height": 18},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["visual_brand"].exists)

    def test_visual_brand_ignores_uniform_component_pattern_text(self) -> None:
        nodes = [
            NormalizedNode(
                id="picker:1",
                name="DatePicker",
                type="FRAME",
                path="Cover > DatePicker",
                depth=2,
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 500},
            )
        ]
        for index in range(6):
            nodes.append(
                NormalizedNode(
                    id=f"text:{index}",
                    name="Date label",
                    type="TEXT",
                    parent_id="picker:1",
                    path="Cover > DatePicker > Date label",
                    depth=3,
                    characters=f"Day {index}",
                    text_style={"fontSize": 20, "fontWeight": 400},
                    absolute_bounding_box={"x": 24 + (index % 3) * 90, "y": 80 + (index // 3) * 44, "width": 70, "height": 24},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["visual_brand"].exists)

    def test_visual_brand_flags_visible_component_workspace_in_screen_board(self) -> None:
        nodes = [
            NormalizedNode(
                id="0:1",
                name="Canvas",
                type="CANVAS",
                path="Canvas",
                depth=0,
            ),
            NormalizedNode(
                id="board:1",
                name="Feedback Form-2",
                type="FRAME",
                parent_id="0:1",
                path="Canvas > Feedback Form-2",
                depth=1,
                absolute_bounding_box={"x": 0, "y": 0, "width": 982, "height": 2077},
            ),
            NormalizedNode(
                id="phone:1",
                name="Feedback",
                type="FRAME",
                parent_id="board:1",
                path="Canvas > Feedback Form-2 > Feedback",
                depth=2,
                absolute_bounding_box={"x": 290, "y": 520, "width": 410, "height": 624},
            ),
            NormalizedNode(
                id="set:rating",
                name="Rating Scale",
                type="COMPONENT_SET",
                parent_id="board:1",
                path="Canvas > Feedback Form-2 > Rating Scale",
                depth=2,
                absolute_bounding_box={"x": 40, "y": 1244, "width": 400, "height": 720},
            ),
            NormalizedNode(
                id="set:submit",
                name="Submit button",
                type="COMPONENT_SET",
                parent_id="board:1",
                path="Canvas > Feedback Form-2 > Submit button",
                depth=2,
                absolute_bounding_box={"x": 480, "y": 1380, "width": 390, "height": 150},
            ),
        ]
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        visual_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "visual_brand"
        ]

        self.assertEqual(len(visual_issues), 1)
        self.assertEqual(visual_issues[0].location.node_id, "set:rating")
        self.assertEqual(visual_issues[0].evidence["detector_id"], "flat_visual_hierarchy")
        self.assertEqual(
            visual_issues[0].evidence["visual_brand_subdetector"],
            "visible_component_workspace",
        )
        self.assertEqual(visual_issues[0].evidence["component_set_count"], 2)

    def test_visual_brand_flags_bad_foreground_panel_position(self) -> None:
        nodes = [
            NormalizedNode(
                id="0:1",
                name="Canvas",
                type="CANVAS",
                path="Canvas",
                depth=0,
            ),
            NormalizedNode(
                id="board:1",
                name="Feedback Form-2",
                type="FRAME",
                parent_id="0:1",
                path="Canvas > Feedback Form-2",
                depth=1,
                absolute_bounding_box={"x": 0, "y": 0, "width": 982, "height": 2077},
            ),
            NormalizedNode(
                id="bg:1",
                name="BG",
                type="FRAME",
                parent_id="board:1",
                path="Canvas > Feedback Form-2 > BG",
                depth=2,
                absolute_bounding_box={"x": 90, "y": 450, "width": 800, "height": 743},
            ),
            NormalizedNode(
                id="bg-rect:1",
                name="Background rectangle",
                type="RECTANGLE",
                parent_id="bg:1",
                path="Canvas > Feedback Form-2 > BG > Background rectangle",
                depth=3,
                fills=[
                    {
                        "type": "SOLID",
                        "color": {"r": 0.85, "g": 0.94, "b": 1.0, "a": 1},
                    }
                ],
                absolute_bounding_box={"x": 90, "y": 450, "width": 800, "height": 743},
            ),
            NormalizedNode(
                id="heading:1",
                name="Feedback Form",
                type="TEXT",
                parent_id="bg:1",
                path="Canvas > Feedback Form-2 > BG > Feedback Form",
                depth=3,
                characters="Feedback Form",
                text_style={"fontSize": 32, "fontWeight": 700},
                absolute_bounding_box={"x": 360, "y": 480, "width": 260, "height": 39},
            ),
            NormalizedNode(
                id="panel:1",
                name="Feedback",
                type="FRAME",
                parent_id="board:1",
                path="Canvas > Feedback Form-2 > Feedback",
                depth=2,
                fills=[
                    {
                        "type": "SOLID",
                        "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                    }
                ],
                absolute_bounding_box={"x": 290, "y": 530, "width": 410, "height": 624},
            ),
        ]
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        visual_issues = [
            issue
            for issue in detection_result.draft_issues
            if issue.evidence.get("visual_brand_subdetector") == "bad_foreground_panel_position"
        ]

        self.assertEqual(len(visual_issues), 1)
        self.assertEqual(visual_issues[0].location.node_id, "panel:1")
        self.assertEqual(visual_issues[0].evidence["heading_to_panel_gap"], 11)
        self.assertGreater(visual_issues[0].evidence["panel_height_to_background_ratio"], 0.8)

    def test_visual_brand_flags_unbalanced_visual_weight(self) -> None:
        nodes = [
            NormalizedNode(
                id="screen:1",
                name="Promo screen",
                type="FRAME",
                path="Promo screen",
                depth=0,
                fills=[
                    {
                        "type": "SOLID",
                        "color": {"r": 1, "g": 1, "b": 1, "a": 1},
                        "opacity": 1,
                    }
                ],
                absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
            ),
            NormalizedNode(
                id="hero:block",
                name="Dominant visual",
                type="RECTANGLE",
                parent_id="screen:1",
                path="Promo screen > Dominant visual",
                depth=1,
                fills=[
                    {
                        "type": "SOLID",
                        "color": {"r": 0, "g": 0, "b": 0, "a": 1},
                        "opacity": 1,
                    }
                ],
                absolute_bounding_box={"x": 0, "y": 0, "width": 220, "height": 220},
            ),
        ]
        for index in range(3):
            nodes.append(
                NormalizedNode(
                    id=f"accent:{index}",
                    name="Small accent",
                    type="RECTANGLE",
                    parent_id="screen:1",
                    path="Promo screen > Small accent",
                    depth=1,
                    fills=[
                        {
                            "type": "SOLID",
                            "color": {"r": 0.8, "g": 0.8, "b": 0.8, "a": 1},
                            "opacity": 1,
                        }
                    ],
                    absolute_bounding_box={"x": 310, "y": 120 + index * 80, "width": 28, "height": 28},
                )
            )
        normalized_file = NormalizedFigmaFile(file_key="file123", nodes=nodes)

        detection_result = run_detections(normalized_file, workers=1)
        visual_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "visual_brand"
        ]

        self.assertEqual(len(visual_issues), 1)
        self.assertEqual(visual_issues[0].evidence["visual_brand_subdetector"], "unbalanced_visual_weight")
        self.assertGreaterEqual(
            visual_issues[0].evidence["aesthetic_checks"]["imbalance_ratio"],
            0.28,
        )

    def test_task_execution_ignores_date_time_picker_without_completion_action(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="picker:1",
                    name="DatePicker",
                    type="INSTANCE",
                    path="DatePicker",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 490},
                ),
                NormalizedNode(
                    id="date:field",
                    name="Date field",
                    type="FRAME",
                    parent_id="picker:1",
                    path="DatePicker > Date field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 80, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="date:text",
                    name="Date label",
                    type="TEXT",
                    parent_id="date:field",
                    path="DatePicker > Date field > Date label",
                    depth=2,
                    characters="June 2022",
                    absolute_bounding_box={"x": 40, "y": 96, "width": 110, "height": 20},
                ),
                NormalizedNode(
                    id="time:field",
                    name="Time field",
                    type="FRAME",
                    parent_id="picker:1",
                    path="DatePicker > Time field",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 148, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="time:text",
                    name="Time label",
                    type="TEXT",
                    parent_id="time:field",
                    path="DatePicker > Time field > Time label",
                    depth=2,
                    characters="9:41 AM",
                    absolute_bounding_box={"x": 40, "y": 164, "width": 110, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["task_execution"].exists)

    def test_content_microcopy_flags_placeholder_with_plain_language_evidence(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Profile screen",
                    type="FRAME",
                    path="Profile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:heading",
                    name="Heading",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Profile screen > Heading",
                    depth=1,
                    characters="Heading",
                    absolute_bounding_box={"x": 24, "y": 88, "width": 130, "height": 28},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        content_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "content_microcopy"
        ]

        self.assertEqual(len(content_issues), 1)
        self.assertEqual(content_issues[0].evidence["content_microcopy_subdetector"], "placeholder_text")
        self.assertEqual(
            content_issues[0].evidence["validation_method"],
            "content_simplification_static_figma_gate",
        )
        self.assertEqual(content_issues[0].evidence["plain_language_checks"]["matched_text"], "heading")

    def test_content_microcopy_flags_generic_cta_without_visible_task_context(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Mobile screen",
                    type="FRAME",
                    path="Mobile screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:continue",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Mobile screen > Primary button",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 240, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:continue",
                    name="Button label",
                    type="TEXT",
                    parent_id="button:continue",
                    path="Mobile screen > Primary button > Button label",
                    depth=2,
                    characters="Continue",
                    absolute_bounding_box={"x": 154, "y": 256, "width": 86, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        content_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "content_microcopy"
        ]

        self.assertEqual(len(content_issues), 1)
        self.assertEqual(
            content_issues[0].evidence["content_microcopy_subdetector"],
            "generic_cta_without_context",
        )
        self.assertFalse(
            content_issues[0].evidence["plain_language_checks"]["has_visible_task_context"]
        )

    def test_content_microcopy_accepts_generic_cta_when_task_context_is_visible(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Create account screen",
                    type="FRAME",
                    path="Create account screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="heading:create",
                    name="Heading",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Create account screen > Heading",
                    depth=1,
                    characters="Create account",
                    absolute_bounding_box={"x": 24, "y": 88, "width": 220, "height": 28},
                ),
                NormalizedNode(
                    id="button:continue",
                    name="Primary button",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Create account screen > Primary button",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 240, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:continue",
                    name="Button label",
                    type="TEXT",
                    parent_id="button:continue",
                    path="Create account screen > Primary button > Button label",
                    depth=2,
                    characters="Continue",
                    absolute_bounding_box={"x": 154, "y": 256, "width": 86, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["content_microcopy"].exists)

    def test_content_microcopy_flags_vague_value_copy(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Welcome screen",
                    type="FRAME",
                    path="Welcome screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:value",
                    name="Hero copy",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Welcome screen > Hero copy",
                    depth=1,
                    characters="Unlock a powerful seamless experience that transforms everything for everyone",
                    absolute_bounding_box={"x": 24, "y": 88, "width": 340, "height": 56},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        content_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "content_microcopy"
        ]

        self.assertEqual(len(content_issues), 1)
        self.assertEqual(content_issues[0].evidence["content_microcopy_subdetector"], "vague_value_copy")
        self.assertGreaterEqual(
            len(content_issues[0].evidence["plain_language_checks"]["vague_terms"]),
            2,
        )

    def test_content_microcopy_flags_truncated_visible_copy(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Menu screen",
                    type="FRAME",
                    path="Menu screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:truncated",
                    name="Product title",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Menu screen > Product title",
                    depth=1,
                    characters="Double cheeseburger with...",
                    absolute_bounding_box={"x": 24, "y": 96, "width": 140, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        content_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "content_microcopy"
        ]

        self.assertEqual(len(content_issues), 1)
        self.assertEqual(
            content_issues[0].evidence["content_microcopy_subdetector"],
            "truncated_or_clipped_copy",
        )

    def test_content_microcopy_ignores_clear_concrete_copy(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Delivery screen",
                    type="FRAME",
                    path="Delivery screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:clear",
                    name="Body copy",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Delivery screen > Body copy",
                    depth=1,
                    characters="Track delayed deliveries and contact the driver from the order screen.",
                    absolute_bounding_box={"x": 24, "y": 88, "width": 340, "height": 44},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["content_microcopy"].exists)

    def test_market_alignment_flags_placeholder_offer_identity_with_value_evidence(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="App store offer screen",
                    type="FRAME",
                    path="App store offer screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:name",
                    name="App name",
                    type="TEXT",
                    parent_id="screen:1",
                    path="App store offer screen > App name",
                    depth=1,
                    characters="App Name",
                    absolute_bounding_box={"x": 24, "y": 96, "width": 140, "height": 28},
                ),
                NormalizedNode(
                    id="text:subtitle",
                    name="Subtitle",
                    type="TEXT",
                    parent_id="screen:1",
                    path="App store offer screen > Subtitle",
                    depth=1,
                    characters="Subtitle",
                    absolute_bounding_box={"x": 24, "y": 132, "width": 180, "height": 22},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        market_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "market_alignment"
        ]

        self.assertEqual(len(market_issues), 1)
        self.assertEqual(market_issues[0].evidence["market_alignment_subdetector"], "placeholder_offer_identity")
        self.assertEqual(
            market_issues[0].evidence["validation_method"],
            "value_communication_static_figma_gate",
        )
        self.assertIn(
            "app name",
            market_issues[0].evidence["value_communication_checks"]["placeholder_identity_hits"],
        )

    def test_market_alignment_does_not_flag_abstract_value_without_visible_placeholder(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Landing screen",
                    type="FRAME",
                    path="Landing screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:value",
                    name="Hero value",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Landing screen > Hero value",
                    depth=1,
                    characters="Unlock a seamless experience that transforms everything",
                    absolute_bounding_box={"x": 24, "y": 96, "width": 340, "height": 56},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        market_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "market_alignment"
        ]

        self.assertEqual(len(market_issues), 0)

    def test_market_alignment_does_not_flag_cta_without_visible_placeholder(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Offer screen",
                    type="FRAME",
                    path="Offer screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="button:cta",
                    name="Primary CTA",
                    type="FRAME",
                    parent_id="screen:1",
                    path="Offer screen > Primary CTA",
                    depth=1,
                    absolute_bounding_box={"x": 24, "y": 260, "width": 345, "height": 52},
                ),
                NormalizedNode(
                    id="text:cta",
                    name="CTA label",
                    type="TEXT",
                    parent_id="button:cta",
                    path="Offer screen > Primary CTA > CTA label",
                    depth=2,
                    characters="Get started",
                    absolute_bounding_box={"x": 148, "y": 276, "width": 98, "height": 20},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)
        market_issues = [
            issue for issue in detection_result.draft_issues if issue.criterion == "market_alignment"
        ]

        self.assertEqual(len(market_issues), 0)

    def test_market_alignment_accepts_concrete_offer_with_proof_and_cta(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="screen:1",
                    name="Delivery platform landing screen",
                    type="FRAME",
                    path="Delivery platform landing screen",
                    depth=0,
                    absolute_bounding_box={"x": 0, "y": 0, "width": 393, "height": 812},
                ),
                NormalizedNode(
                    id="text:audience",
                    name="Audience headline",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Delivery platform landing screen > Audience headline",
                    depth=1,
                    characters="For delivery teams",
                    absolute_bounding_box={"x": 24, "y": 96, "width": 250, "height": 32},
                ),
                NormalizedNode(
                    id="text:value",
                    name="Value copy",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Delivery platform landing screen > Value copy",
                    depth=1,
                    characters="Track delayed orders and manage driver messages from one dashboard.",
                    absolute_bounding_box={"x": 24, "y": 140, "width": 340, "height": 48},
                ),
                NormalizedNode(
                    id="text:proof",
                    name="Proof",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Delivery platform landing screen > Proof",
                    depth=1,
                    characters="Trusted by 1,200 drivers",
                    absolute_bounding_box={"x": 24, "y": 212, "width": 260, "height": 24},
                ),
                NormalizedNode(
                    id="text:cta",
                    name="CTA",
                    type="TEXT",
                    parent_id="screen:1",
                    path="Delivery platform landing screen > CTA",
                    depth=1,
                    characters="Book demo",
                    absolute_bounding_box={"x": 24, "y": 268, "width": 110, "height": 24},
                ),
            ],
        )

        detection_result = run_detections(normalized_file, workers=1)

        self.assertFalse(detection_result.status_by_id()["market_alignment"].exists)


class AuditExtractionTests(unittest.TestCase):
    def test_audit_extraction_contains_complete_normalized_input_and_indexes(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="frame:1",
                    name="Frame",
                    type="FRAME",
                    path="Frame",
                    depth=0,
                    absolute_bounding_box={
                        "x": 0,
                        "y": 0,
                        "width": 200,
                        "height": 100,
                    },
                ),
                NormalizedNode(
                    id="text:1",
                    name="Title",
                    type="TEXT",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > Title",
                    depth=1,
                    characters="Hello",
                    style={"fontSize": 16},
                    text_style={"fontSize": 16},
                ),
            ],
        )

        extraction = build_audit_extraction(normalized_file)
        rebuilt = normalized_file_from_audit_extraction(extraction)

        self.assertEqual(extraction["counts"]["nodes"], 2)
        self.assertEqual(extraction["counts"]["text_nodes"], 1)
        self.assertEqual(extraction["indexes"]["children_by_parent"]["frame:1"], ["text:1"])
        self.assertEqual(extraction["audit_views"]["text_content"][0]["characters"], "Hello")
        self.assertEqual([node.id for node in rebuilt.nodes], ["frame:1", "text:1"])


class FakeImageClient:
    def get_image_urls(
        self,
        file_key: str,
        node_ids: list[str],
        *,
        image_format: str = "png",
        scale: float = 2.0,
    ) -> dict[str, str]:
        return {node_ids[0]: "memory://rendered-frame.png"}

    def download_binary(self, url: str) -> bytes:
        image = Image.new("RGB", (200, 100), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class FailingImageClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_image_urls(
        self,
        file_key: str,
        node_ids: list[str],
        *,
        image_format: str = "png",
        scale: float = 2.0,
    ) -> dict[str, str]:
        self.calls += 1
        raise RuntimeError("render failed")

    def download_binary(self, url: str) -> bytes:
        raise AssertionError("download should not be called")


class CountingImageClient:
    def __init__(self) -> None:
        self.url_calls = 0
        self.download_calls = 0

    def get_image_urls(
        self,
        file_key: str,
        node_ids: list[str],
        *,
        image_format: str = "png",
        scale: float = 2.0,
    ) -> dict[str, str]:
        self.url_calls += 1
        return {node_id: f"memory://{node_id}.png" for node_id in node_ids}

    def download_binary(self, url: str) -> bytes:
        self.download_calls += 1
        image = Image.new("RGB", (200, 100), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class AnnotationTests(unittest.TestCase):
    def test_calculate_rectangle_pixels_maps_figma_coordinates(self) -> None:
        rectangle = calculate_rectangle_pixels(
            target_box={"x": 10, "y": 5, "width": 20, "height": 10},
            render_box={"x": 0, "y": 0, "width": 100, "height": 50},
            image_width=200,
            image_height=100,
        )

        self.assertEqual(rectangle.x, 20)
        self.assertEqual(rectangle.y, 10)
        self.assertEqual(rectangle.width, 40)
        self.assertEqual(rectangle.height, 20)

    def test_annotate_issue_links_image_artifact_to_issue(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="frame:1",
                    name="Frame",
                    type="FRAME",
                    path="Frame",
                    depth=0,
                    absolute_bounding_box={
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                ),
                NormalizedNode(
                    id="node:2",
                    name="Button",
                    type="FRAME",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > Button",
                    depth=1,
                    absolute_bounding_box={
                        "x": 10,
                        "y": 5,
                        "width": 20,
                        "height": 10,
                    },
                    absolute_render_bounds={
                        "x": 9,
                        "y": 4,
                        "width": 22,
                        "height": 12,
                    },
                ),
            ],
        )
        issue = AuditIssue(
            id="issue-1",
            severity=Severity.HIGH,
            message="Example issue",
            location=IssueLocation(node_id="node:2", node_name="Button"),
        )
        audit_result = AuditResult(issues=[issue])

        with TemporaryDirectory() as temp_dir:
            annotate_issue_screenshots(
                normalized_file=normalized_file,
                audit_result=audit_result,
                output_dir=Path(temp_dir),
                client=FakeImageClient(),  # type: ignore[arg-type]
            )

            artifact = audit_result.issues[0].visual_evidence[0]
            self.assertTrue(Path(artifact.image_path).exists())
            self.assertEqual(artifact.target_node_id, "node:2")
            self.assertEqual(artifact.render_node_id, "frame:1")
            self.assertEqual(artifact.coordinate_source, "absoluteRenderBounds")
            self.assertEqual(artifact.rectangle_px.x, 18)
            self.assertEqual(artifact.rectangle_px.y, 8)
            self.assertEqual(artifact.rectangle_px.width, 44)
            self.assertEqual(artifact.rectangle_px.height, 24)

    def test_annotation_reuses_versioned_render_cache_without_figma_call(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            version="version-1",
            nodes=[
                NormalizedNode(
                    id="frame:1",
                    name="Frame",
                    type="FRAME",
                    path="Frame",
                    depth=0,
                    absolute_bounding_box={
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                ),
                NormalizedNode(
                    id="node:2",
                    name="Button",
                    type="FRAME",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > Button",
                    depth=1,
                    absolute_bounding_box={
                        "x": 10,
                        "y": 5,
                        "width": 20,
                        "height": 10,
                    },
                ),
            ],
        )

        def make_result(issue_id: str) -> AuditResult:
            return AuditResult(
                issues=[
                    AuditIssue(
                        id=issue_id,
                        severity=Severity.HIGH,
                        message="Example issue",
                        location=IssueLocation(node_id="node:2", node_name="Button"),
                    )
                ]
            )

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first_client = CountingImageClient()
            first_result = make_result("issue-1")
            annotate_issue_screenshots(
                normalized_file=normalized_file,
                audit_result=first_result,
                output_dir=output_dir,
                client=first_client,  # type: ignore[arg-type]
            )

            second_client = FailingImageClient()
            second_result = make_result("issue-2")
            annotate_issue_screenshots(
                normalized_file=normalized_file,
                audit_result=second_result,
                output_dir=output_dir,
                client=second_client,  # type: ignore[arg-type]
            )

        self.assertEqual(first_client.url_calls, 1)
        self.assertEqual(first_client.download_calls, 1)
        self.assertEqual(second_client.calls, 0)
        self.assertEqual(len(second_result.issues[0].visual_evidence), 1)

    def test_annotation_max_images_limits_render_attempts_not_only_successes(self) -> None:
        normalized_file = NormalizedFigmaFile(
            file_key="file123",
            nodes=[
                NormalizedNode(
                    id="frame:1",
                    name="Frame",
                    type="FRAME",
                    path="Frame",
                    depth=0,
                    absolute_bounding_box={
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    },
                ),
                NormalizedNode(
                    id="node:1",
                    name="First",
                    type="TEXT",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > First",
                    depth=1,
                    absolute_bounding_box={
                        "x": 10,
                        "y": 5,
                        "width": 20,
                        "height": 10,
                    },
                ),
                NormalizedNode(
                    id="node:2",
                    name="Second",
                    type="TEXT",
                    parent_id="frame:1",
                    frame_id="frame:1",
                    frame_name="Frame",
                    path="Frame > Second",
                    depth=1,
                    absolute_bounding_box={
                        "x": 40,
                        "y": 5,
                        "width": 20,
                        "height": 10,
                    },
                ),
            ],
        )
        issues = [
            AuditIssue(
                id="issue-1",
                severity=Severity.LOW,
                message="First",
                location=IssueLocation(node_id="node:1"),
            ),
            AuditIssue(
                id="issue-2",
                severity=Severity.LOW,
                message="Second",
                location=IssueLocation(node_id="node:2"),
            ),
        ]
        audit_result = AuditResult(issues=issues)
        client = FailingImageClient()

        with TemporaryDirectory() as temp_dir:
            annotate_issue_screenshots(
                normalized_file=normalized_file,
                audit_result=audit_result,
                output_dir=Path(temp_dir),
                client=client,  # type: ignore[arg-type]
                max_images=1,
            )

        self.assertEqual(client.calls, 1)
        self.assertIn("Could not request rendered image", issues[0].evidence["annotation_warning"])
        self.assertIn("max_images=1", issues[1].evidence["annotation_warning"])


class BrowserScreenshotTests(unittest.TestCase):
    def test_real_page_capture_adds_geometry_artifact_for_report_callouts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            extraction_path = root / "audit_info.json"
            output_dir = root / "real_pages"
            save_json(
                extraction_path,
                {
                    "normalized_file": {
                        "nodes": [
                            {
                                "id": "1:1",
                                "name": "Screen",
                                "type": "FRAME",
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 200,
                                    "height": 100,
                                },
                            },
                            {
                                "id": "1:2",
                                "name": "Low contrast label",
                                "type": "TEXT",
                                "parent_id": "1:1",
                                "absolute_bounding_box": {
                                    "x": 20,
                                    "y": 30,
                                    "width": 60,
                                    "height": 12,
                                },
                            },
                        ]
                    }
                },
            )
            save_json(
                detections_path,
                {
                    "summary": {"screenshot_count": 0},
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "location": {"node_id": "1:2"},
                            "evidence": {},
                            "visual_evidence": [],
                        }
                    ],
                },
            )

            def fake_capture_url(
                *,
                url: str,
                output_path: Path,
                width: int,
                height: int,
                timeout_seconds: int,
            ) -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (width, height), "white").save(output_path)

            with patch(
                "figma_audit.browser_screenshots._capture_url",
                side_effect=fake_capture_url,
            ):
                screenshots = capture_real_page_screenshots(
                    source_url="https://www.figma.com/proto/ABC123/File?node-id=1-2",
                    detections_path=detections_path,
                    extraction_path=extraction_path,
                    output_dir=output_dir,
                    width=400,
                    height=300,
                    log=None,
                )

            updated = load_json(detections_path)
            issue = updated["draft_issues"][0]
            artifact = issue["visual_evidence"][0]
            self.assertEqual(len(screenshots), 1)
            self.assertEqual(artifact["type"], "real_page_geometry_screenshot")
            self.assertEqual(artifact["target_node_id"], "1:2")
            self.assertEqual(artifact["render_node_id"], "1:1")
            self.assertTrue(Path(artifact["image_path"]).exists())
            self.assertEqual(updated["summary"]["screenshot_count"], 1)

    def test_real_page_capture_captures_all_large_root_frames_even_without_issues(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            extraction_path = root / "audit_info.json"
            output_dir = root / "real_pages"
            save_json(
                extraction_path,
                {
                    "normalized_file": {
                        "nodes": [
                            {"id": "0:1", "name": "Canvas", "type": "CANVAS"},
                            {
                                "id": "1:1",
                                "name": "Feedback Form-1",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 320,
                                    "height": 700,
                                },
                            },
                            {
                                "id": "2:1",
                                "name": "Feedback Form-2",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 420,
                                    "y": 0,
                                    "width": 320,
                                    "height": 700,
                                },
                            },
                            {
                                "id": "3:1",
                                "name": "Feedback Form-3",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 840,
                                    "y": 0,
                                    "width": 320,
                                    "height": 700,
                                },
                            },
                            {
                                "id": "1:2",
                                "name": "Low contrast label",
                                "type": "TEXT",
                                "parent_id": "1:1",
                                "absolute_bounding_box": {
                                    "x": 20,
                                    "y": 30,
                                    "width": 80,
                                    "height": 16,
                                },
                            },
                        ]
                    }
                },
            )
            save_json(
                detections_path,
                {
                    "summary": {"screenshot_count": 0},
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "location": {"node_id": "1:2"},
                            "evidence": {},
                            "visual_evidence": [],
                        }
                    ],
                },
            )

            def fake_capture_url(
                *,
                url: str,
                output_path: Path,
                width: int,
                height: int,
                timeout_seconds: int,
            ) -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (width, height), "white").save(output_path)

            with patch(
                "figma_audit.browser_screenshots._capture_url",
                side_effect=fake_capture_url,
            ):
                screenshots = capture_real_page_screenshots(
                    source_url="https://www.figma.com/proto/ABC123/File?node-id=0-1",
                    detections_path=detections_path,
                    extraction_path=extraction_path,
                    output_dir=output_dir,
                    width=400,
                    height=300,
                    log=None,
                )

            names = {path.name for path in screenshots}
            self.assertEqual(len(screenshots), 3)
            self.assertIn("real_figma_page__1-1__Feedback-Form-1.png", names)
            self.assertIn("real_figma_page__2-1__Feedback-Form-2.png", names)
            self.assertIn("real_figma_page__3-1__Feedback-Form-3.png", names)

    def test_real_page_capture_removes_unvisible_issues_from_client_report_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            extraction_path = root / "audit_info.json"
            output_dir = root / "real_pages"
            save_json(
                extraction_path,
                {
                    "normalized_file": {
                        "nodes": [
                            {
                                "id": "1:1",
                                "name": "Screen",
                                "type": "FRAME",
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 200,
                                    "height": 100,
                                },
                            },
                            {
                                "id": "1:2",
                                "name": "Off-screen label",
                                "type": "TEXT",
                                "parent_id": "1:1",
                                "absolute_bounding_box": {
                                    "x": 260,
                                    "y": 30,
                                    "width": 60,
                                    "height": 12,
                                },
                            },
                        ]
                    }
                },
            )
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 0,
                    },
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "criterion": "trust_accessibility",
                            "location": {"node_id": "1:2"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                            },
                            "visual_evidence": [],
                        }
                    ],
                },
            )

            def fake_capture_url(
                *,
                url: str,
                output_path: Path,
                width: int,
                height: int,
                timeout_seconds: int,
            ) -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (width, height), "white").save(output_path)

            with patch(
                "figma_audit.browser_screenshots._capture_url",
                side_effect=fake_capture_url,
            ):
                capture_real_page_screenshots(
                    source_url="https://www.figma.com/proto/ABC123/File?node-id=1-2",
                    detections_path=detections_path,
                    extraction_path=extraction_path,
                    output_dir=output_dir,
                    width=400,
                    height=300,
                    log=None,
                )

            updated = load_json(detections_path)

        self.assertEqual(updated["draft_issues"], [])
        self.assertEqual(updated["summary"]["draft_issue_count"], 0)
        self.assertEqual(updated["summary"]["screenshot_count"], 0)
        self.assertFalse(updated["criterion_status"][0]["exists"])

    def test_real_page_capture_removes_hidden_nodes_from_client_report_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            extraction_path = root / "audit_info.json"
            output_dir = root / "real_pages"
            save_json(
                extraction_path,
                {
                    "normalized_file": {
                        "nodes": [
                            {
                                "id": "1:1",
                                "name": "Screen",
                                "type": "FRAME",
                                "visible": False,
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 200,
                                    "height": 100,
                                },
                            },
                            {
                                "id": "1:2",
                                "name": "Hidden label",
                                "type": "TEXT",
                                "parent_id": "1:1",
                                "absolute_bounding_box": {
                                    "x": 20,
                                    "y": 30,
                                    "width": 60,
                                    "height": 12,
                                },
                            },
                        ]
                    }
                },
            )
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 0,
                    },
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "criterion": "trust_accessibility",
                            "location": {"node_id": "1:2"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                            },
                            "visual_evidence": [],
                        }
                    ],
                },
            )

            def fake_capture_url(
                *,
                url: str,
                output_path: Path,
                width: int,
                height: int,
                timeout_seconds: int,
            ) -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (width, height), "white").save(output_path)

            with patch(
                "figma_audit.browser_screenshots._capture_url",
                side_effect=fake_capture_url,
            ):
                capture_real_page_screenshots(
                    source_url="https://www.figma.com/proto/ABC123/File?node-id=1-2",
                    detections_path=detections_path,
                    extraction_path=extraction_path,
                    output_dir=output_dir,
                    width=400,
                    height=300,
                    log=None,
                )

            updated = load_json(detections_path)

        self.assertEqual(updated["draft_issues"], [])
        self.assertEqual(updated["summary"]["draft_issue_count"], 0)
        self.assertEqual(updated["summary"]["screenshot_count"], 0)
        self.assertFalse(updated["criterion_status"][0]["exists"])


class ReportTests(unittest.TestCase):
    def test_build_detection_review_report_contains_image_without_review_controls(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "annotation.png"
            Image.new("RGB", (20, 10), "white").save(image_path)

            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "task_execution",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["example_detector"],
                        },
                        {
                            "criterion_id": "flow_architecture",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                        {
                            "criterion_id": "ui_consistency",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                        {
                            "criterion_id": "visual_brand",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                        {
                            "criterion_id": "content_microcopy",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                        {
                            "criterion_id": "market_alignment",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        },
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "axis": "task_execution",
                            "criterion": "task_execution",
                            "severity": "high",
                            "message": "Example draft issue",
                            "location": {
                                "page_name": None,
                                "frame_name": "Frame",
                                "node_id": "1:2",
                                "node_name": "Button",
                                "path": "Frame > Button",
                            },
                            "evidence": {
                                "detector_id": "example_detector",
                                "confidence": "high",
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_geometry_screenshot",
                                    "image_path": str(image_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {
                                        "x": 1,
                                        "y": 2,
                                        "width": 3,
                                        "height": 4,
                                    },
                                    "image_width": 20,
                                    "image_height": 10,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "absoluteBoundingBox",
                                    "accuracy": "test",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 7,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("UX/UI Draft Detection Review", html)
            self.assertIn("Visible Criteria", html)
            self.assertIn("Clear completion path", html)
            self.assertIn("Primary content readability", html)
            self.assertIn("Decision label readability", html)
            self.assertIn("rule-based", html)
            self.assertIn("AI-assisted", html)
            self.assertIn("human-review", html)
            self.assertIn("Example draft issue", html)
            self.assertIn("issue-1-callout.png", html)
            self.assertTrue((output_path.parent / "callouts" / "issue-1-callout.png").exists())
            self.assertNotIn("Export Review JSON", html)
            self.assertNotIn("value=\"accepted\"", html)
            self.assertNotIn("Reviewer note", html)
            self.assertNotIn("<dt>Node ID</dt>", html)

    def test_report_uses_client_clear_copy_for_market_alignment(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "client_view.png"
            Image.new("RGB", (80, 120), "white").save(image_path)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "market_alignment",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["generic_offer_without_proof"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "market-issue-1",
                            "axis": "market_alignment",
                            "criterion": "market_alignment",
                            "severity": "high",
                            "message": "A large visible area has weak market-alignment signals.",
                            "location": {
                                "frame_name": "AppStore",
                                "node_id": "1:2",
                                "node_name": "Company Name",
                                "path": "AppStore > Company Name",
                            },
                            "evidence": {
                                "detector_id": "generic_offer_without_proof",
                                "confidence": "high",
                                "mobile_viewport_name": "AppStore",
                                "market_alignment_subdetector": "placeholder_offer_identity",
                                "text_sample": "Company Name",
                                "visible_placeholder_identity": "Company Name",
                                "value_communication_checks": {
                                    "value_layer_count": 2,
                                    "placeholder_identity_hits": ["company name"],
                                    "abstract_value_hits": [],
                                    "has_proof": True,
                                    "has_commercial_cta": False,
                                },
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_geometry_screenshot",
                                    "image_path": str(image_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {
                                        "x": 0,
                                        "y": 0,
                                        "width": 80,
                                        "height": 120,
                                    },
                                    "image_width": 80,
                                    "image_height": 120,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "test",
                                    "accuracy": "real_page_screenshot_with_static_figma_geometry",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 0,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Placeholder identity text is visible", html)
            self.assertIn("visible placeholder identity text is still present", html)
            self.assertIn("market-issue-1-callout.png", html)
            self.assertIn("Replace the boxed placeholder", html)

    def test_report_hides_rejected_client_view_text_findings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "hidden-heading",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast appears below the recommended threshold.",
                            "location": {
                                "frame_name": "Heading",
                                "node_id": "1:2",
                                "node_name": "Heading",
                                "path": "Screen > Icon > Heading",
                            },
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                                "text_sample": "Heading",
                                "contrast_ratio": 1.0,
                                "required_ratio": 4.5,
                                "client_visibility": "rendered_text_foreground_fills_target",
                                "client_visibility_note": (
                                    "Removed from client-facing findings because the mapped target did not match "
                                    "the detector condition in the client-view screenshot."
                                ),
                            },
                            "visual_evidence": [],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 0,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("No issue-level finding is shown", html)
            self.assertNotIn("Text is hard to read", html)
            self.assertNotIn("The affected text is &quot;Heading&quot;", html)

    def test_report_hides_findings_disproved_by_rendered_screenshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "client_view.png"
            Image.new("RGB", (80, 120), "white").save(image_path)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "contrast-disproved",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast appears below the recommended threshold.",
                            "location": {"frame_name": "Menu", "node_id": "1:2", "node_name": "Label"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                                "text_sample": "Hot",
                                "contrast_ratio": 1.6,
                                "required_ratio": 4.5,
                                "client_visibility": "rendered_target_contrast_is_not_low",
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_geometry_screenshot",
                                    "image_path": str(image_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {"x": 10, "y": 10, "width": 20, "height": 10},
                                    "image_width": 80,
                                    "image_height": 120,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "test",
                                    "accuracy": "real_page_screenshot_with_static_figma_geometry",
                                    "notes": [],
                                    "client_view_validation": {
                                        "rejected_reason": "rendered_target_contrast_is_not_low",
                                        "rendered_contrast_estimate": 7.2,
                                    },
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("No issue-level finding is shown", html)
            self.assertNotIn("Text is hard to read", html)
            self.assertNotIn("contrast-disproved-callout.png", html)

    def test_report_scores_findings_without_real_client_visual_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "flow_architecture",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "medium",
                            "detector_ids": ["generic_navigation_label"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "nav-without-screenshot",
                            "axis": "flow_architecture",
                            "criterion": "flow_architecture",
                            "severity": "medium",
                            "message": "Navigation uses generic labels.",
                            "location": {"frame_name": "Tabs", "node_name": "Label"},
                            "evidence": {
                                "detector_id": "generic_navigation_label",
                                "confidence": "medium",
                                "flow_subdetector": "generic_or_repeated_destination_labels",
                                "navigation_labels": ["Label", "Label", "Label"],
                                "duplicated_labels": {"label": 3},
                            },
                            "visual_evidence": [],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 0,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("nav-without-screenshot", html)
            self.assertIn("Navigation labels are too generic", html)
            self.assertIn("static Figma evidence", html)
            self.assertNotIn('data-score="10.0" data-score-role="overall"', html)

    def test_report_hides_context_only_broad_visible_findings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "client_view.png"
            Image.new("RGB", (120, 160), "white").save(image_path)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "broad-contrast-issue",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast is hard to read.",
                            "location": {"frame_name": "Offer", "node_id": "1:2", "node_name": "Content"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                                "text_sample": "Body copy",
                                "contrast_ratio": 1.2,
                                "required_ratio": 4.5,
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_context_screenshot",
                                    "image_path": str(image_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {"x": 0, "y": 0, "width": 120, "height": 160},
                                    "image_width": 120,
                                    "image_height": 160,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "test",
                                    "accuracy": "real_page_screenshot_with_broad_visible_context",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("broad-contrast-issue", html)
            self.assertIn("Text is hard to read", html)
            self.assertIn("Supporting visual evidence", html)
            self.assertNotIn("broad-contrast-issue-callout.png", html)

    def test_ai_review_rejects_unsupported_broad_issue_from_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "client_view.png"
            Image.new("RGB", (120, 160), "white").save(image_path)
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "visual_brand",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "low",
                            "detector_ids": ["flat_visual_hierarchy"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "broad-brand-issue",
                            "axis": "visual_brand",
                            "criterion": "visual_brand",
                            "severity": "low",
                            "message": "A broad visual issue was detected.",
                            "location": {"frame_name": "Home", "node_id": "1:2", "node_name": "Hero"},
                            "evidence": {
                                "detector_id": "flat_visual_hierarchy",
                                "confidence": "low",
                                "visual_brand_subdetector": "flat_text_hierarchy",
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_context_screenshot",
                                    "image_path": str(image_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {"x": 0, "y": 0, "width": 120, "height": 160},
                                    "image_width": 120,
                                    "image_height": 160,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "test",
                                    "accuracy": "real_page_screenshot_with_broad_visible_context",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            fake_response = type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reviews": [
                                        {
                                            "id": "broad-brand-issue",
                                            "decision": "reject",
                                            "confidence": 0.81,
                                            "reason": "The evidence is too broad for a client-facing visual-brand failure.",
                                            "client_reframe": "",
                                            "recommended_focus": "Inspect manually.",
                                        }
                                    ]
                                }
                            )
                        }
                    },
                    "text": "",
                },
            )()

            with patch("figma_audit.ai_reviewer.OLLAMA_API_KEY", "test-key"), patch(
                "figma_audit.ai_reviewer.requests.post",
                return_value=fake_response,
            ):
                result_path = build_detection_review_report(
                    detections_path=detections_path,
                    output_path=output_path,
                    polish_copy=False,
                    ai_review=True,
                    force_polish=True,
                )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("No issue-level finding is shown", html)
            self.assertNotIn("A broad visual issue was detected", html)

    def test_report_does_not_present_locator_preview_as_client_screenshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            locator_path = root / "locator.png"
            Image.new("RGB", (20, 10), "white").save(locator_path)

            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "task_execution",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["example_detector"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "axis": "task_execution",
                            "criterion": "task_execution",
                            "severity": "high",
                            "message": "Example draft issue",
                            "location": {
                                "node_id": "1:2",
                                "node_name": "Button",
                            },
                            "evidence": {
                                "detector_id": "example_detector",
                                "confidence": "high",
                            },
                            "visual_evidence": [
                                {
                                    "type": "node_preview_locator",
                                    "image_path": str(locator_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {
                                        "x": 1,
                                        "y": 2,
                                        "width": 3,
                                        "height": 4,
                                    },
                                    "image_width": 20,
                                    "image_height": 10,
                                    "figma_target_bounding_box": {},
                                    "figma_render_bounding_box": {},
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "absoluteBoundingBox",
                                    "accuracy": "focused_node_preview_not_rendered_screenshot",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 7,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Example draft issue", html)
            self.assertIn("No rendered screenshot yet", html)
            self.assertNotIn("Static Figma locator", html)
            self.assertNotIn("Client-view screenshot", html)
            self.assertFalse((output_path.parent / "callouts" / "issue-1-callout.png").exists())
            self.assertNotIn('data-score="10.0" data-score-role="overall"', html)

    def test_report_uses_existing_unmarked_real_page_screenshots(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_pages_dir = root / "real_pages"
            real_pages_dir.mkdir()
            Image.new("RGB", (120, 160), "white").save(
                real_pages_dir / "real_figma_page__10-65__Feedback-Form-3.png"
            )
            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        }
                    ],
                    "draft_issues": [],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 0,
                        "draft_issue_count": 0,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Public prototype screenshot", html)
            self.assertIn("Feedback Form 3", html)
            self.assertIn("real_pages/real_figma_page__10-65__Feedback-Form-3.png", html)
            self.assertNotIn("No verified screenshots", html)

    def test_report_pages_scanned_uses_current_large_figma_pages_not_stale_real_pages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            real_pages_dir = reports_dir / "real_pages"
            extracted_dir = root / "extracted"
            real_pages_dir.mkdir(parents=True)
            extracted_dir.mkdir()
            Image.new("RGB", (120, 220), "white").save(
                real_pages_dir / "real_figma_page__1-1__Feedback-Form-1.png"
            )
            Image.new("RGB", (120, 220), "white").save(
                real_pages_dir / "real_figma_page__2-1__Feedback-Form-2.png"
            )
            Image.new("RGB", (120, 220), "white").save(
                real_pages_dir / "real_figma_page__99-1__product-1.png"
            )
            save_json(
                extracted_dir / "audit_info.json",
                {
                    "normalized_file": {
                        "nodes": [
                            {"id": "0:1", "name": "Canvas", "type": "CANVAS"},
                            {
                                "id": "1:1",
                                "name": "Feedback Form-1",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 320,
                                    "height": 700,
                                },
                            },
                            {
                                "id": "2:1",
                                "name": "Feedback Form-2",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 420,
                                    "y": 0,
                                    "width": 320,
                                    "height": 700,
                                },
                            },
                        ]
                    }
                },
            )
            detections_path = root / "detections.json"
            output_path = reports_dir / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": False,
                            "issue_count": 0,
                            "confidence": None,
                            "detector_ids": [],
                        }
                    ],
                    "draft_issues": [],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 0,
                        "draft_issue_count": 0,
                        "screenshot_count": 2,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Feedback Form-1", html)
            self.assertIn("Feedback Form-2", html)
            self.assertIn("Unmarked page screenshot", html)
            self.assertNotIn("product 1", html)
            self.assertNotIn("real_figma_page__99-1__product-1.png", html)

    def test_report_builds_issue_callout_from_existing_page_screenshot_and_extraction(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            real_pages_dir = reports_dir / "real_pages"
            extracted_dir = root / "extracted"
            real_pages_dir.mkdir(parents=True)
            extracted_dir.mkdir()
            Image.new("RGB", (400, 800), "black").save(
                real_pages_dir / "real_figma_page__10-65__Feedback-Form-3.png"
            )
            save_json(
                extracted_dir / "audit_info.json",
                {
                    "normalized_file": {
                        "nodes": [
                            {"id": "0:1", "name": "Canvas", "type": "CANVAS"},
                            {
                                "id": "10:65",
                                "name": "Feedback Form-3",
                                "type": "FRAME",
                                "parent_id": "0:1",
                                "absolute_bounding_box": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 200,
                                    "height": 400,
                                },
                            },
                            {
                                "id": "7:31",
                                "name": "Problem label",
                                "type": "TEXT",
                                "parent_id": "10:65",
                                "absolute_bounding_box": {
                                    "x": 50,
                                    "y": 100,
                                    "width": 60,
                                    "height": 20,
                                },
                            },
                        ]
                    }
                },
            )
            detections_path = root / "detections.json"
            output_path = reports_dir / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast appears below the recommended threshold.",
                            "location": {"frame_name": "Feedback Form-3", "node_id": "7:31"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                                "text_sample": "Problem label",
                            },
                            "visual_evidence": [],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Problem area highlighted", html)
            self.assertIn("callouts/issue-1-callout.png", html)
            self.assertNotIn("No client-view screenshot yet", html)
            self.assertTrue((reports_dir / "callouts" / "issue-1-callout.png").exists())

    def test_report_does_not_use_local_annotation_locator_when_issue_visual_evidence_is_missing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            annotations_dir = root / "annotations"
            annotations_dir.mkdir()
            annotation = Image.new("RGB", (260, 160), "white")
            draw = ImageDraw.Draw(annotation)
            draw.rectangle([70, 60, 170, 86], outline=(255, 0, 0), width=4)
            annotation.save(
                annotations_dir
                / "draft-low-text-contrast-example__node-7-31__locator.png"
            )
            detections_path = root / "detections.json"
            output_path = reports_dir / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast appears below the recommended threshold.",
                            "location": {"node_id": "7:31"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                            },
                            "visual_evidence": [],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("No rendered screenshot yet", html)
            self.assertNotIn("Static Figma locator", html)
            self.assertNotIn("callouts/issue-1-callout.png", html)
            self.assertFalse((reports_dir / "callouts" / "issue-1-callout.png").exists())

    def test_report_builds_issue_callout_from_cached_figma_render(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            normalized_dir = root / "normalized"
            render_cache_dir = root / "annotations" / "_render_cache"
            normalized_dir.mkdir()
            render_cache_dir.mkdir(parents=True)
            cached_render = Image.new("RGB", (400, 300), "white")
            draw = ImageDraw.Draw(cached_render)
            draw.rounded_rectangle([80, 120, 260, 180], radius=12, fill=(236, 242, 246))
            draw.text((105, 140), "Add your comments", fill=(80, 80, 80))
            cached_render.save(
                render_cache_dir / "file__v-1__node-6-15__png__scale-2.png"
            )
            save_json(
                normalized_dir / "normalized_file.json",
                {
                    "nodes": [
                        {"id": "0:1", "name": "Canvas", "type": "CANVAS"},
                        {
                            "id": "6:15",
                            "name": "Feedback Form-1",
                            "type": "FRAME",
                            "parent_id": "0:1",
                            "absolute_bounding_box": {
                                "x": 0,
                                "y": 0,
                                "width": 200,
                                "height": 150,
                            },
                        },
                        {
                            "id": "6:234",
                            "name": "Add your comments",
                            "type": "TEXT",
                            "parent_id": "6:15",
                            "absolute_bounding_box": {
                                "x": 50,
                                "y": 60,
                                "width": 95,
                                "height": 15,
                            },
                        },
                    ]
                },
            )
            detections_path = root / "detections.json"
            output_path = reports_dir / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "content_microcopy",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["placeholder_or_generic_copy"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-1",
                            "axis": "content_microcopy",
                            "criterion": "content_microcopy",
                            "severity": "medium",
                            "message": "Placeholder copy is visible.",
                            "location": {"node_id": "6:234"},
                            "evidence": {
                                "detector_id": "placeholder_or_generic_copy",
                                "confidence": "high",
                                "text_sample": "Add your comments",
                            },
                            "visual_evidence": [],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            result_path = build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )

            html = result_path.read_text(encoding="utf-8")
            self.assertIn("Problem area highlighted", html)
            self.assertIn("callouts/issue-1-callout.png", html)
            self.assertNotIn("No rendered screenshot yet", html)
            self.assertNotIn("Static Figma locator", html)
            self.assertTrue((reports_dir / "callouts" / "issue-1-callout.png").exists())

    def test_report_callout_crop_stays_inside_detected_design_area(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_screenshot_path = root / "real_page.png"
            screenshot = Image.new("RGB", (400, 800), "black")
            for x in range(100, 300):
                for y in range(200, 500):
                    screenshot.putpixel((x, y), (255, 255, 255))
            screenshot.save(real_screenshot_path)

            detections_path = root / "detections.json"
            output_path = root / "report.html"
            save_json(
                detections_path,
                {
                    "criterion_status": [
                        {
                            "criterion_id": "trust_accessibility",
                            "exists": True,
                            "issue_count": 1,
                            "confidence": "high",
                            "detector_ids": ["low_text_contrast"],
                        }
                    ],
                    "draft_issues": [
                        {
                            "id": "issue-real-crop",
                            "axis": "trust_accessibility",
                            "criterion": "trust_accessibility",
                            "severity": "high",
                            "message": "Text contrast appears below the recommended threshold.",
                            "location": {"node_id": "1:2", "node_name": "Label"},
                            "evidence": {
                                "detector_id": "low_text_contrast",
                                "confidence": "high",
                                "contrast_gap": 3.2,
                                "real_page_screenshot_path": str(real_screenshot_path),
                                "real_page_screenshot_node_name": "Screen",
                            },
                            "visual_evidence": [
                                {
                                    "type": "real_page_geometry_screenshot",
                                    "image_path": str(real_screenshot_path),
                                    "target_node_id": "1:2",
                                    "render_node_id": "1:1",
                                    "rectangle_px": {
                                        "x": 180,
                                        "y": 460,
                                        "width": 20,
                                        "height": 12,
                                    },
                                    "image_width": 400,
                                    "image_height": 800,
                                    "figma_target_bounding_box": {
                                        "x": 80,
                                        "y": 260,
                                        "width": 20,
                                        "height": 12,
                                    },
                                    "figma_render_bounding_box": {
                                        "x": 0,
                                        "y": 0,
                                        "width": 200,
                                        "height": 300,
                                    },
                                    "coordinate_strategy": "test",
                                    "coordinate_source": "absoluteBoundingBox",
                                    "accuracy": "real_page_screenshot_with_static_figma_geometry",
                                    "notes": [],
                                }
                            ],
                        }
                    ],
                    "summary": {
                        "criteria_total": 1,
                        "criteria_with_detected_problems": 1,
                        "draft_issue_count": 1,
                        "screenshot_count": 1,
                        "status": "draft_detections_not_final_audit",
                    },
                },
            )

            build_detection_review_report(
                detections_path=detections_path,
                output_path=output_path,
                polish_copy=False,
            )
            callout = Image.open(output_path.parent / "callouts" / "issue-real-crop-callout.png").convert("RGB")
            pixels = list(callout.getdata())
            black_ratio = sum(1 for pixel in pixels if max(pixel) < 8) / len(pixels)

        self.assertLess(black_ratio, 0.05)

    def test_ollama_polisher_generates_client_copy_and_caches(self) -> None:
        class FakeOllamaResponse:
            status_code = 200
            text = ""

            def json(self) -> dict[str, object]:
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "issues": [
                                    {
                                        "id": "issue-1",
                                        "title": "Improve text contrast",
                                        "what_is_wrong": "The label blends into the surrounding surface.",
                                        "why_it_matters": "Users may miss important information.",
                                        "recommended_fix": "Increase the foreground/background contrast and retest the component.",
                                    }
                                ]
                            }
                        )
                    }
                }

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = load_criteria_catalog()
            criteria_by_id = {criterion.id: criterion for criterion in catalog.criteria}
            issues: list[dict[str, object]] = [
                {
                    "id": "issue-1",
                    "criterion": "trust_accessibility",
                    "severity": "high",
                    "message": "Text contrast appears below the recommended threshold.",
                    "location": {"frame_name": "Frame", "node_name": "Label"},
                    "evidence": {
                        "detector_id": "low_text_contrast",
                        "confidence": "high",
                        "contrast_ratio": 2.1,
                        "required_ratio": 4.5,
                    },
                }
            ]

            with patch("figma_audit.report_polisher.OLLAMA_API_KEY", "test-key"), patch(
                "figma_audit.report_polisher.OLLAMA_API_HOST",
                "https://ollama.com",
            ), patch("figma_audit.report_polisher.OLLAMA_REPORT_MODEL", "gpt-oss:120b"), patch(
                "figma_audit.report_polisher.requests.post",
                return_value=FakeOllamaResponse(),
            ) as post:
                polished = polish_report_copy_with_ollama(
                    issues=issues,
                    criteria_by_id=criteria_by_id,
                    cache_path=root / "client_copy.json",
                    log=None,
                )

            self.assertEqual(polished["issue-1"]["title"], "Improve text contrast")
            self.assertEqual(load_json(root / "client_copy.json")["model"], "gpt-oss:120b")
            self.assertEqual(post.call_args.args[0], "https://ollama.com/api/chat")
            self.assertEqual(
                post.call_args.kwargs["headers"]["Authorization"],
                "Bearer test-key",
            )
            self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-oss:120b")
            self.assertFalse(post.call_args.kwargs["json"]["stream"])


if __name__ == "__main__":
    unittest.main()
