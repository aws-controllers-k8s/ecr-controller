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
    # Allow override via environment variable
    env_region = os.getenv('ECR_REPLICATION_DESTINATION_REGION')
    if env_region:
        return env_region
    
    # Default mappings to opposite coast
    east_coast_regions = ['us-east-1', 'us-east-2']
    west_coast_regions = ['us-west-1', 'us-west-2']
    
    if current_region in east_coast_regions:
        return 'us-west-2'
    elif current_region in west_coast_regions:
        return 'us-east-2'
    else:
        # For other regions, default to us-west-2
        return 'us-west-2'

@service_marker
class TestReplicationConfiguration:

    def get_registry_replication_configuration(self, ecr_client) -> Optional[dict]:
        """Get the current replication configuration for the registry."""
        try:
            resp = ecr_client.describe_registry()
            return resp.get('replicationConfiguration', None)
        except Exception as e:
            logging.error(f"Failed to get replication configuration: {e}")
            return None

    def test_create_replication_configuration(self, ecr_client):
        """Test creating a registry-level replication configuration."""
        resource_name = random_suffix_name("replication-config", 24)
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        destination_region = get_default_destination_region(current_region)
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPLICATION_CONFIG_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["REPOSITORY_PREFIX"] = repository_prefix
        replacements["DESTINATION_REGION"] = destination_region

        resource_data = load_ecr_resource(
            "replication_configuration",
            additional_replacements=replacements,
        )
        logging.debug(f"Resource data: {resource_data}")

        # Create k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        assert k8s.get_resource_exists(ref)

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
        cr = k8s.get_resource(ref)
        assert cr is not None
        assert 'status' in cr
        
        # Clean up
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

    def test_update_replication_configuration(self, ecr_client):
        """Test updating a registry-level replication configuration."""
        resource_name = random_suffix_name("replication-config", 24)
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        initial_region = get_default_destination_region(current_region)
        # For update test, use the opposite of the initial destination
        updated_region = 'us-east-2' if initial_region.startswith('us-west') else 'us-west-2'
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPLICATION_CONFIG_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["REPOSITORY_PREFIX"] = repository_prefix
        replacements["DESTINATION_REGION"] = initial_region

        resource_data = load_ecr_resource(
            "replication_configuration",
            additional_replacements=replacements,
        )

        # Create k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Verify initial creation
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
                            "registryId": registry_id
                        }],
                        "repositoryFilters": [{
                            "filter": repository_prefix,
                            "filterType": "PREFIX_MATCH"
                        }]
                    }]
                }
            }
        }
        k8s.patch_custom_resource(ref, updates)
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

        # Clean up
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

    def test_delete_replication_configuration(self, ecr_client):
        """Test deleting a registry-level replication configuration."""
        resource_name = random_suffix_name("replication-config", 24)
        registry_id = get_account_id()
        current_region = get_region()
        repository_prefix = "test-repo"
        destination_region = get_default_destination_region(current_region)
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPLICATION_CONFIG_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["REPOSITORY_PREFIX"] = repository_prefix
        replacements["DESTINATION_REGION"] = destination_region

        resource_data = load_ecr_resource(
            "replication_configuration",
            additional_replacements=replacements,
        )

        # Create k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Verify replication was created
        replication_config = self.get_registry_replication_configuration(ecr_client)
        assert replication_config is not None
        initial_rules_count = len(replication_config.get('rules', []))
        assert initial_rules_count > 0

        # Delete the k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Verify the resource no longer exists in Kubernetes
        assert not k8s.get_resource_exists(ref)

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

    def test_multiple_replication_rules(self, ecr_client):
        """Test creating a replication configuration with multiple rules."""
        resource_name = random_suffix_name("replication-config", 24)
        registry_id = get_account_id()
        current_region = get_region()
        destination_region1 = get_default_destination_region(current_region)
        # Use the opposite region for the second rule
        destination_region2 = 'us-east-2' if destination_region1.startswith('us-west') else 'us-west-2'
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPLICATION_CONFIG_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id

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
                                {"region": destination_region1, "registryId": registry_id}
                            ],
                            "repositoryFilters": [
                                {"filter": "prod-", "filterType": "PREFIX_MATCH"}
                            ]
                        },
                        {
                            "destinations": [
                                {"region": destination_region2, "registryId": registry_id}
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