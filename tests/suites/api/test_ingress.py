"""
External API tests for ingress upload endpoint.

These tests validate the data upload API contract by making direct HTTP calls
through the gateway with JWT authentication.

Jira Test Cases:
- FLPATH-3171: Verify ingress upload endpoint accepts valid payloads
- FLPATH-3172: Verify ingress upload rejects invalid payloads
"""

import io
import tarfile
import json
import uuid
from datetime import datetime, timezone

import pytest
import requests


# MIME type expected by insights-ingress-go for cost management uploads
UPLOAD_MIME_TYPE = "application/vnd.redhat.hccm.filename+tgz"


def create_upload_package(cluster_id: str) -> bytes:
    """Create a valid upload tarball matching the format expected by ingress.
    
    The ingress service (insights-ingress-go) expects:
    - MIME type: application/vnd.redhat.hccm.filename+tgz
    - Tarball containing manifest.json and CSV files
    - Manifest with specific fields including cluster_id, files, start, end
    
    Args:
        cluster_id: Unique cluster identifier
        
    Returns:
        bytes: Gzipped tarball content
    """
    now = datetime.now(timezone.utc)
    
    # CSV with required columns for OCP cost data
    csv_content = f"""report_period_start,report_period_end,interval_start,interval_end,namespace,pod,node
{now.isoformat()},{now.isoformat()},{now.isoformat()},{now.isoformat()},test-ns,test-pod,test-node
"""
    
    # Manifest matching the format used by cost-management-operator
    manifest = {
        "uuid": str(uuid.uuid4()),
        "cluster_id": cluster_id,
        "cluster_alias": f"api-test-{cluster_id[-8:]}",
        "date": now.isoformat(),
        "files": ["openshift_usage_report.csv"],
        "certified": True,
        "operator_version": "1.0.0",
        "daily_reports": False,
        "start": now.isoformat(),
        "end": now.isoformat(),
    }
    
    # Create tarball in memory
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        # Add manifest
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        manifest_info = tarfile.TarInfo(name="manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))
        
        # Add CSV file (name must match manifest.files)
        csv_bytes = csv_content.encode("utf-8")
        csv_info = tarfile.TarInfo(name="openshift_usage_report.csv")
        csv_info.size = len(csv_bytes)
        tar.addfile(csv_info, io.BytesIO(csv_bytes))
    
    buffer.seek(0)
    return buffer.read()


@pytest.mark.api
@pytest.mark.component
class TestIngressUpload:
    """Test ingress upload endpoint via external gateway route."""

    def test_upload_endpoint_accessible(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify ingress upload endpoint accepts valid payloads.
        
        FLPATH-3171: Verify ingress upload endpoint accepts valid payloads
        
        The ingress service (insights-ingress-go) accepts cost management data
        uploads and returns 202 Accepted when the file is queued for processing.
        
        Tests:
        - Endpoint accepts valid tarball with correct MIME type
        - Returns 202 Accepted
        """
        cluster_id = f"api-test-{uuid.uuid4().hex[:8]}"
        payload = create_upload_package(cluster_id)
        
        response = authenticated_session.post(
            f"{gateway_url}/ingress/v1/upload",
            files={"file": ("cost-mgmt.tar.gz", payload, UPLOAD_MIME_TYPE)},
            timeout=60,
        )
        
        assert response.status_code == 202, (
            f"Expected 202 Accepted, got {response.status_code}: {response.text}"
        )



@pytest.mark.api
@pytest.mark.component
class TestIngressValidation:
    """Test ingress upload validation."""

    def test_upload_invalid_content_type(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify upload with invalid content type is rejected.
        
        FLPATH-3172: Verify ingress upload rejects invalid payloads
        
        The ingress service returns 400 Bad Request when the payload
        is not a valid multipart form with a file attachment.
        
        Tests:
        - Invalid content type is rejected
        - Returns 400 Bad Request
        """
        response = authenticated_session.post(
            f"{gateway_url}/ingress/v1/upload",
            data="not a tarball",
            headers={"Content-Type": "text/plain"},
            timeout=60,
        )
        
        # Ingress returns 400 for invalid content type
        assert response.status_code == 400, (
            f"Expected 400 Bad Request, got {response.status_code}: {response.text}"
        )

    def test_upload_wrong_mime_type(
        self,
        authenticated_session: requests.Session,
        gateway_url: str,
    ):
        """Verify upload with wrong MIME type is rejected.
        
        The ingress service returns 415 Unsupported Media Type when a file
        is submitted with an incorrect MIME type (application/gzip instead of
        the required application/vnd.redhat.hccm.filename+tgz).
        
        Tests:
        - Invalid MIME type is rejected
        - Returns 415 Unsupported Media Type
        """
        response = authenticated_session.post(
            f"{gateway_url}/ingress/v1/upload",
            files={"file": ("test.tar.gz", b"fake content", "application/gzip")},
            timeout=60,
        )
        
        # Ingress returns 415 for invalid MIME type
        assert response.status_code == 415, (
            f"Expected 415 Unsupported Media Type, got {response.status_code}: {response.text}"
        )
