import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app
from app.extensions import db as db_extension
from app.models import InsuranceCompany, PolicyType

@pytest.fixture(scope='session')
def app():
    """Create and configure a new app instance for each test session."""
    # Create the app with the testing configuration
    app = create_app('testing')

    # Establish an application context
    with app.app_context():
        # Create all tables
        db_extension.create_all()

        # Seed insurance companies if not already present
        if InsuranceCompany.query.count() == 0:
            from app.services.insurance_company_service import InsuranceCompanyService
            InsuranceCompanyService.ensure_seeded()

        # Seed policy types if not already present
        if PolicyType.query.count() == 0:
            from app.services.policy_type_service import PolicyTypeService
            # Seed some basic policy types for testing
            PolicyTypeService.add_policy_type('Motor Comprehensive', 'Comprehensive motor vehicle coverage', 5000.0)
            PolicyTypeService.add_policy_type('Motor Third Party', 'Third party only motor vehicle coverage', 3000.0)
            PolicyTypeService.add_policy_type('Public Liability', 'Public liability insurance for businesses', 4000.0)

        yield app

        # Clean up: drop all tables
        db_extension.drop_all()

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@pytest.fixture
def runner(app):
    """A test runner for the app's Click commands."""
    return app.test_cli_runner()