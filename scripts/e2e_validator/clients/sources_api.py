"""
Sources API Client

Provides methods to interact with the Sources API for Day 2 operations:
- Create/manage sources (cost data providers)
- Create/manage applications (link sources to Cost Management)
- Manage authentication

This is the proper Red Hat-supported way to add providers to Cost Management.
"""

import json
import requests
from typing import Dict, Optional, List
import time


class SourcesAPIClient:
    """Client for interacting with Sources API"""

    def __init__(self, base_url: str = "http://localhost:8001", org_id: str = "org1234567"):
        """Initialize Sources API client

        Args:
            base_url: Sources API base URL (default: port-forwarded localhost)
            org_id: Organization ID for x-rh-sources-org-id header
        """
        self.base_url = base_url.rstrip('/')
        self.org_id = org_id
        self.api_base = f"{self.base_url}/api/sources/v3.1"

        # Default headers for all requests
        self.headers = {
            'Content-Type': 'application/json',
            'x-rh-sources-org-id': org_id,
            # For on-prem, we might need to add basic auth or other headers
        }

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make HTTP request to Sources API

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            path: API path (e.g., '/sources')
            **kwargs: Additional arguments for requests (json, params, etc.)

        Returns:
            Response object
        """
        url = f"{self.api_base}{path}"
        kwargs.setdefault('headers', {}).update(self.headers)

        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def get_source_types(self) -> List[Dict]:
        """Get available source types

        Returns:
            List of source type dicts
        """
        response = self._request('GET', '/source_types')
        return response.json().get('data', [])

    def get_source_type_id(self, name: str = "amazon") -> Optional[str]:
        """Get source type ID by name

        Args:
            name: Source type name (amazon, azure, gcp, etc.)

        Returns:
            Source type ID or None
        """
        source_types = self.get_source_types()
        for st in source_types:
            if st.get('name') == name:
                return st.get('id')
        return None

    def get_application_types(self) -> List[Dict]:
        """Get available application types

        Returns:
            List of application type dicts
        """
        response = self._request('GET', '/application_types')
        return response.json().get('data', [])

    def get_application_type_id(self, name: str = "/insights/platform/cost-management") -> Optional[str]:
        """Get application type ID by name

        Args:
            name: Application type name (default: Cost Management)

        Returns:
            Application type ID or None
        """
        app_types = self.get_application_types()
        for at in app_types:
            if at.get('name') == name or at.get('display_name') == name:
                return at.get('id')
        return None

    def create_source(self,
                     name: str,
                     source_type_name: str = "amazon",
                     source_ref: Optional[str] = None) -> Dict:
        """Create a new source

        Args:
            name: Source name (e.g., "AWS Test Provider E2E")
            source_type_name: Source type (amazon, azure, gcp, etc.)
            source_ref: External reference (optional, e.g., AWS account ID)

        Returns:
            Created source dict
        """
        # Get source type ID
        source_type_id = self.get_source_type_id(source_type_name)
        if not source_type_id:
            raise ValueError(f"Source type '{source_type_name}' not found")

        payload = {
            "name": name,
            "source_type_id": source_type_id,
        }

        if source_ref:
            payload["source_ref"] = source_ref

        response = self._request('POST', '/sources', json=payload)
        return response.json()

    def create_authentication(self,
                            source_id: str,
                            auth_type: str = "cloud-meter-arn",
                            username: Optional[str] = None,
                            password: Optional[str] = None) -> Dict:
        """Create authentication for a source

        Args:
            source_id: Source ID
            auth_type: Authentication type (cloud-meter-arn for AWS, etc.)
            username: Username/ARN (optional)
            password: Password/credentials (optional)

        Returns:
            Created authentication dict
        """
        payload = {
            "resource_type": "Source",
            "resource_id": source_id,
            "authtype": auth_type,
        }

        if username:
            payload["username"] = username
        if password:
            payload["password"] = password

        response = self._request('POST', '/authentications', json=payload)
        return response.json()

    def create_application(self,
                          source_id: str,
                          application_type_name: str = "/insights/platform/cost-management",
                          extra: Optional[Dict] = None) -> Dict:
        """Create an application linking a source to Cost Management

        Args:
            source_id: Source ID to link
            application_type_name: Application type name
            extra: Additional application configuration (e.g., bucket, report_name)

        Returns:
            Created application dict
        """
        # Get application type ID
        app_type_id = self.get_application_type_id(application_type_name)
        if not app_type_id:
            raise ValueError(f"Application type '{application_type_name}' not found")

        payload = {
            "source_id": source_id,
            "application_type_id": app_type_id,
        }

        if extra:
            payload["extra"] = extra

        response = self._request('POST', '/applications', json=payload)
        return response.json()

    def get_sources(self, filters: Optional[Dict] = None) -> List[Dict]:
        """Get all sources

        Args:
            filters: Optional filters (e.g., {'filter[name]': 'my-source'})

        Returns:
            List of source dicts
        """
        params = filters or {}
        response = self._request('GET', '/sources', params=params)
        return response.json().get('data', [])

    def get_source_by_name(self, name: str) -> Optional[Dict]:
        """Get source by name

        Args:
            name: Source name

        Returns:
            Source dict or None
        """
        sources = self.get_sources({'filter[name]': name})
        return sources[0] if sources else None

    def delete_source(self, source_id: str) -> None:
        """Delete a source

        Args:
            source_id: Source ID to delete
        """
        self._request('DELETE', f'/sources/{source_id}')

    def create_aws_source_full(self,
                               name: str = "AWS Test Provider E2E",
                               bucket: str = "cost-data",
                               report_name: str = "test-report",
                               report_prefix: str = "") -> Dict:
        """Create a complete AWS source with authentication and application

        This is a convenience method that:
        1. Creates the source
        2. Creates authentication (minimal/dummy for on-prem)
        3. Creates the Cost Management application with S3 config

        Args:
            name: Source name
            bucket: S3 bucket name
            report_name: Cost and Usage Report name
            report_prefix: Report prefix in bucket

        Returns:
            Dict with source, auth, and application info
        """
        print(f"Creating AWS source via Sources API: {name}")

        # Step 1: Create source
        print(f"  Creating source...")
        source = self.create_source(name=name, source_type_name="amazon")
        source_id = source['id']
        print(f"    ✓ Source created: {source_id}")

        # Step 2: Create authentication (minimal for on-prem)
        print(f"  Creating authentication...")
        try:
            auth = self.create_authentication(
                source_id=source_id,
                auth_type="cloud-meter-arn",
                username="arn:aws:iam::123456789012:role/CostManagementRole"  # Dummy ARN
            )
            print(f"    ✓ Authentication created: {auth['id']}")
        except Exception as e:
            print(f"    ⚠️  Authentication creation failed (may not be required): {e}")
            auth = None

        # Step 3: Create Cost Management application with S3 config
        print(f"  Creating Cost Management application...")
        app_extra = {
            "bucket": bucket,
            "report_name": report_name,
        }
        if report_prefix:
            app_extra["report_prefix"] = report_prefix

        application = self.create_application(
            source_id=source_id,
            extra=app_extra
        )
        print(f"    ✓ Application created: {application['id']}")

        print(f"  ✅ AWS source fully configured")

        return {
            'source': source,
            'authentication': auth,
            'application': application,
            'source_id': source_id,
            'application_id': application['id']
        }

    def wait_for_source_availability(self, source_id: str, timeout: int = 60) -> bool:
        """Wait for source to become available

        Args:
            source_id: Source ID
            timeout: Timeout in seconds

        Returns:
            True if available, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self._request('GET', f'/sources/{source_id}')
            source = response.json()
            status = source.get('availability_status')

            if status == 'available':
                return True
            elif status in ['unavailable', 'partially_available']:
                print(f"  Source status: {status}")

            time.sleep(5)

        return False

