# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
# 	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for ECR Registry-level Replication Configuration.
"""

import pytest
import time
import logging
from typing import Dict, Optional

from acktest.resources import random_suffix_name
from acktest.aws.identity import get_region, get_account_id
from acktest.k8s import resource as k8s
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES
import os

RESOURCE_PLURAL = "replicationconfigurations"

CREATE_WAIT_AFTER_SECONDS = 15
UPDATE_WAIT_AFTER_SECONDS = 15
DELETE_WAIT_AFTER_SECONDS = 15

def get_default_destination_region(current_region: str) -> str:
    """Get the default destination region (opposite coast) for replication.
    
    Args:
        current_region: The current AWS region
        
    Returns:
        Default destination region on the opposite coast
    """
    logging.info(f"Current region for replication: {current_region}")
    
    # Allow override via environment variable
    env_region = os.getenv('ECR_REPLICATION_DESTINATION_REGION')
    if env_region:
        logging.info(f"Using environment override destination region: {env_region}")
        if env_region == current_region:
            logging.warning(f"Environment destination region {env_region} is same as current region {current_region}, falling back to default logic")
        else:
            return env_region
    
    # Default mappings to opposite coast
    east_coast_regions = ['us-east-1', 'us-east-2']
    west_coast_regions = ['us-west-1', 'us-west-2']
    
    destination = None
    if current_region in east_coast_regions:
        destination = 'us-west-2'
    elif current_region in west_coast_regions:
        destination = 'us-east-2'
    else:
        # For other regions, default to us-east-2 to ensure different from us-west-2 default
        destination = 'us-east-2'
    
    # Safety check to ensure destination is different from source
    if destination == current_region:
        # Fallback mapping to ensure we never get the same region
        if current_region == 'us-east-2':
            destination = 'us-west-2'
        else:
            destination = 'us-east-2'
    
    logging.info(f"Selected destination region: {destination}")
    return destination

@service_marker
@pytest.mark.order("last")  # Run this test class after others to avoid conflicts
class TestReplicationConfiguration:
    
    # Class-level shared resource name to ensure singleton behavior
    _shared_resource_name = None
    _shared_resource_ref = None

    def clear_registry_replication_configuration(self, ecr_client):
        """Clear the registry replication configuration to ensure clean test state."""
        try:
            # Clear replication configuration by setting empty rules
            ecr_client.put_replication_configuration(
                replicationConfiguration={'rules': []}
            )
            logging.info("Cleared registry replication configuration")
        except Exception as e:
            logging.warning(f"Failed to clear replication configuration: {e}")

    @classmethod
    def setup_class(cls):
        """Setup before the entire test class."""
        # Clear any existing replication configuration before the class
        import boto3
        ecr_client = boto3.client("ecr")
        test_instance = cls()
        test_instance.clear_registry_replication_configuration(ecr_client)
        # Wait a bit for the clear operation to propagate
        time.sleep(5)
        
        # Generate a single resource name for the entire test class
        cls._shared_resource_name = random_suffix_name("replication-config", 24)
        cls._shared_resource_ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            cls._shared_resource_name, namespace="default",
        )

    @classmethod
    def teardown_class(cls):
        """Teardown after the entire test class."""
        # Clean up the shared resource if it exists
        if cls._shared_resource_ref and k8s.get_resource_exists(cls._shared_resource_ref):
            try:
                k8s.delete_custom_resource(cls._shared_resource_ref)
                time.sleep(DELETE_WAIT_AFTER_SECONDS)
            except Exception as e:
                logging.warning(f"Failed to delete shared resource: {e}")
        
        # Clear replication configuration after the entire test class
        import boto3
        ecr_client = boto3.client("ecr")
        test_instance = cls()
        test_instance.clear_registry_replication_configuration(ecr_client)
        # Wait a bit for the clear operation to propagate
        time.sleep(5)

    def get_registry_replication_configuration(self, ecr_client) -> Optional[dict]:
        """Get the current replication configuration for the registry."""
        try:
            resp = ecr_client.describe_registry()
            return resp.get('replicationConfiguration', None)
        except Exception as e:
            logging.error(f"Failed to get replication configuration: {e}")
            return None

    @pytest.mark.order(1)
    def test_create_replication_configuration(self, ecr_client):
        """Test creating a registry-level replication configuration."""
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        destination_region = get_default_destination_region(current_region)
        
        # Ensure destination is different from source region
        assert destination_region != current_region, f"Destination region {destination_region} cannot be same as source region {current_region}"
        
        logging.info(f"Test setup - Source region: {current_region}, Destination region: {destination_region}")
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPLICATION_CONFIG_NAME"] = self._shared_resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["REPOSITORY_PREFIX"] = repository_prefix
        replacements["DESTINATION_REGION"] = destination_region

        resource_data = load_ecr_resource(
            "replication_configuration",
            additional_replacements=replacements,
        )
        logging.debug(f"Resource data: {resource_data}")

        # Create k8s resource using shared reference
        k8s.create_custom_resource(self._shared_resource_ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(self._shared_resource_ref)

        assert cr is not None
        assert k8s.get_resource_exists(self._shared_resource_ref)

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Check replication configuration was created in AWS
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        assert 'rules' in replication_config
        assert len(replication_config['rules']) > 0
        
        # Find our rule
        found_rule = False
        for rule in replication_config['rules']:
            if 'repositoryFilters' in rule:
                for filter_item in rule['repositoryFilters']:
                    if (filter_item.get('filter') == repository_prefix and 
                        filter_item.get('filterType') == 'PREFIX_MATCH'):
                        found_rule = True
                        # Check destinations
                        assert 'destinations' in rule
                        assert len(rule['destinations']) > 0
                        dest = rule['destinations'][0]
                        assert dest['region'] == destination_region
                        assert dest['registryId'] == registry_id
                        break
        
        assert found_rule, "Replication rule not found in AWS registry"

        # Verify the Kubernetes resource status
        cr = k8s.get_resource(self._shared_resource_ref)
        assert cr is not None
        assert 'status' in cr
        
        # Don't clean up here - let class teardown handle it

    @pytest.mark.order(2)
    def test_update_replication_configuration(self, ecr_client):
        """Test updating a registry-level replication configuration."""
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        initial_region = get_default_destination_region(current_region)
        # For update test, use a different region from both source and initial destination
        # Map regions to ensure three different regions: source, initial destination, updated destination
        region_alternatives = {
            'us-west-2': 'us-east-1',
            'us-west-1': 'us-east-1', 
            'us-east-2': 'us-west-1',
            'us-east-1': 'us-west-1'
        }
        
        # Get updated region that's different from both current and initial
        updated_region = region_alternatives.get(current_region, 'us-east-1')
        
        # If updated region equals initial region, use alternative
        if updated_region == initial_region:
            updated_region = 'us-west-1' if initial_region != 'us-west-1' else 'us-east-1'
        
        # Safety check to ensure regions are different from source
        assert initial_region != current_region, f"Initial region {initial_region} cannot be same as source region {current_region}"
        assert updated_region != current_region, f"Updated region {updated_region} cannot be same as source region {current_region}"
        
        logging.info(f"Update test setup - Source region: {current_region}, Initial destination: {initial_region}, Updated destination: {updated_region}")
        
        # Verify the shared resource already exists from the create test
        assert k8s.get_resource_exists(self._shared_resource_ref), "Shared resource should exist from create test"

        # Verify initial state from create test
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        assert len(replication_config.get('rules', [])) > 0

        # Update replication configuration - change destination region
        updates = {
            "spec": {
                "replicationConfiguration": {
                    "rules": [{
                        "destinations": [{
                            "region": updated_region,
                            "registryID": registry_id
                        }],
                        "repositoryFilters": [{
                            "filter": repository_prefix,
                            "filterType": "PREFIX_MATCH"
                        }]
                    }]
                }
            }
        }
        k8s.patch_custom_resource(self._shared_resource_ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Check updated replication configuration
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        
        # Find updated rule
        found_updated_rule = False
        for rule in replication_config['rules']:
            if 'repositoryFilters' in rule:
                for filter_item in rule['repositoryFilters']:
                    if (filter_item.get('filter') == repository_prefix and 
                        filter_item.get('filterType') == 'PREFIX_MATCH'):
                        found_updated_rule = True
                        # Check updated destination
                        assert rule['destinations'][0]['region'] == updated_region
                        assert rule['destinations'][0]['registryId'] == registry_id
                        break
        
        assert found_updated_rule, "Updated replication rule not found"

        # Don't clean up here - let class teardown handle it

    @pytest.mark.order(3)
    def test_delete_replication_configuration(self, ecr_client):
        """Test deleting a registry-level replication configuration."""
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        destination_region = get_default_destination_region(current_region)
        
        # Safety check to ensure destination is different from source
        assert destination_region != current_region, f"Destination region {destination_region} cannot be same as source region {current_region}"
        
        logging.info(f"Delete test setup - Source region: {current_region}, Destination region: {destination_region}")
        
        # Verify the shared resource exists from previous tests
        assert k8s.get_resource_exists(self._shared_resource_ref), "Shared resource should exist from previous tests"

        # Verify replication was created in previous tests
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        initial_rules_count = len(replication_config.get('rules', []))
        assert initial_rules_count > 0

        # Delete the k8s resource
        _, deleted = k8s.delete_custom_resource(self._shared_resource_ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Verify the resource no longer exists in Kubernetes
        assert not k8s.get_resource_exists(self._shared_resource_ref)

        # Verify replication configuration was cleaned up in AWS
        # Note: The registry replication configuration should be empty or not contain our rule
        replication_config = self.get_registry_replication_configuration(ecr_client)
        if replication_config and 'rules' in replication_config:
            # Check that our specific rule is no longer present
            for rule in replication_config['rules']:
                if 'repositoryFilters' in rule:
                    for filter_item in rule['repositoryFilters']:
                        # Our rule should not be found
                        assert not (filter_item.get('filter') == repository_prefix and 
                                  filter_item.get('filterType') == 'PREFIX_MATCH'), \
                               "Replication rule still exists after deletion"

    @pytest.mark.order(4)
    def test_multiple_replication_rules(self, ecr_client):
        """Test creating a replication configuration with multiple rules.
        
        This test runs after the delete test, so the registry should be clean.
        It creates a new resource to test multiple rules functionality.
        """
        resource_name = random_suffix_name("replication-config-multi", 24)
        registry_id = get_account_id()
        current_region = get_region()
        destination_region1 = get_default_destination_region(current_region)
        # Use different regions for the two rules, ensuring all three are different
        region_alternatives = {
            'us-west-2': 'us-east-2',
            'us-west-1': 'us-east-1', 
            'us-east-2': 'us-west-2',
            'us-east-1': 'us-west-1'
        }
        
        # Get second destination that's different from both current and first destination
        destination_region2 = region_alternatives.get(current_region, 'us-east-1')
        
        # If second destination equals first destination, use alternative
        if destination_region2 == destination_region1:
            destination_region2 = 'us-west-1' if destination_region1 != 'us-west-1' else 'us-east-1'
        
        # Safety checks to ensure regions are different from source
        assert destination_region1 != current_region, f"Destination1 region {destination_region1} cannot be same as source region {current_region}"
        assert destination_region2 != current_region, f"Destination2 region {destination_region2} cannot be same as source region {current_region}"
        
        logging.info(f"Multiple rules test setup - Source region: {current_region}, Destination1: {destination_region1}, Destination2: {destination_region2}")
        
        # Ensure the registry is clean (previous tests should have cleaned up)
        replication_config = self.get_registry_replication_configuration(ecr_client)
        if replication_config and 'rules' in replication_config and replication_config['rules']:
            logging.warning("Registry not clean, clearing replication configuration")
            self.clear_registry_replication_configuration(ecr_client)
            time.sleep(5)

        # Create resource with multiple rules
        resource_data = {
            "apiVersion": f"{CRD_GROUP}/{CRD_VERSION}",
            "kind": "ReplicationConfiguration",
            "metadata": {"name": resource_name},
            "spec": {
                "registryID": registry_id,
                "replicationConfiguration": {
                    "rules": [
                        {
                            "destinations": [
                                {"region": destination_region1, "registryID": registry_id}
                            ],
                            "repositoryFilters": [
                                {"filter": "prod-", "filterType": "PREFIX_MATCH"}
                            ]
                        },
                        {
                            "destinations": [
                                {"region": destination_region2, "registryID": registry_id}
                            ],
                            "repositoryFilters": [
                                {"filter": "test-", "filterType": "PREFIX_MATCH"}
                            ]
                        }
                    ]
                }
            }
        }

        # Create k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Check both rules were created in AWS
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        assert 'rules' in replication_config
        
        rules = replication_config['rules']
        assert len(rules) >= 2, f"Expected at least 2 rules, got {len(rules)}"
        
        # Check for both rules
        found_prod_rule = False
        found_test_rule = False
        
        for rule in rules:
            if 'repositoryFilters' in rule:
                for filter_item in rule['repositoryFilters']:
                    if filter_item.get('filter') == 'prod-':
                        found_prod_rule = True
                        assert rule['destinations'][0]['region'] == destination_region1
                    elif filter_item.get('filter') == 'test-':
                        found_test_rule = True
                        assert rule['destinations'][0]['region'] == destination_region2
        
        assert found_prod_rule, "Production replication rule not found"
        assert found_test_rule, "Test replication rule not found"

        # Clean up
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)