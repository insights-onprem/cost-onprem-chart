"""
Page Object Model for Cost On-Prem UI Tests.

This module provides reusable page objects that encapsulate UI interactions,
following the Page Object Model pattern for better test maintainability.

Usage:
    from .pages import SourcesPage, SourceData, CommonLocators
    
    sources = SourcesPage(page, ui_url)
    sources.navigate()
    sources.create_integration_via_wizard("my-source", "my-cluster-id")
"""

from .common import CommonLocators
from .sources_page import SourceData, SourcesPage

__all__ = ["CommonLocators", "SourceData", "SourcesPage"]
