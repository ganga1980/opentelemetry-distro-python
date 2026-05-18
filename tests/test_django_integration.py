# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for Django instrumentation.

Validates that Django is registered in the distro's supported library lists
and that the DjangoInstrumentor can be loaded.  Full span-generation tests
are skipped when the ``django`` package is not installed.
"""

import unittest

import pytest

django = pytest.importorskip("django")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.django import DjangoInstrumentor  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestDjangoInstrumentationConfig(unittest.TestCase):
    """Verify django is registered in the distro's supported library lists."""

    def test_django_in_supported_libraries(self):
        self.assertIn("django", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestDjangoInstrumentorLifecycle(unittest.TestCase):
    """Verify the DjangoInstrumentor can be loaded and reports dependencies."""

    def test_instrumentation_dependencies(self):
        inst = DjangoInstrumentor()
        deps = inst.instrumentation_dependencies()
        dep_str = " ".join(deps)
        self.assertIn("django", dep_str.lower())


if __name__ == "__main__":
    unittest.main()
