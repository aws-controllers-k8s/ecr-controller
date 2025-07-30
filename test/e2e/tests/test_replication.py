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

"""Integration tests for ECR Repository Replication Configuration.
"""

import pytest
import time
import logging
from typing import Dict, Tuple

from acktest.resources import random_suffix_name
from acktest.aws.identity import get_region, get_account_id
from acktest.k8s import resource as k8s
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = "repositories"

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

@service_marker
class TestReplicationConfiguration:

    def get_repository(self, ecr_client, repository_name: str) -> dict:
        try:
            resp = ecr_client.describe_repositories(
                repositoryNames=[repository_name]
            )
            return resp['repositories'][0]
        except Exception:
            return None

    def get_replication_configuration(self, ecr_client) -> dict:
        try:
            resp = ecr_client.describe_registry()
            return resp.get('replicationConfiguration', None)
        except Exception:
            return None

    def test_create_repository_with_replication(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)
        registry_id = get_account_id()
        repository_prefix = resource_name[:10]
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["REPOSITORY_PREFIX"] = repository_prefix

        resource_data = load_ecr_resource(
            "repository_replication",
            additional_replacements=replacements,
        )
        logging.debug(resource_data)

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

        # Check repository created
        repository = self.get_repository(ecr_client, resource_name)
        assert repository is not None
        assert repository['repositoryName'] == resource_name

        # Check replication configuration
        replication_config = self.get_replication_configuration(ecr_client)
        assert replication_config is not None
        assert 'rules' in replication_config
        assert len(replication_config['rules']) > 0
        
        # Find our rule
        found_rule = False
        for rule in replication_config['rules']:
            if 'repositoryFilters' in rule:
                for filter in rule['repositoryFilters']:
                    if filter.get('filter') == repository_prefix and filter.get('filterType') == 'PREFIX_MATCH':
                        found_rule = True
                        # Check destinations
                        assert 'destinations' in rule
                        assert len(rule['destinations']) > 0
                        assert rule['destinations'][0]['region'] == 'us-west-2'
                        assert rule['destinations'][0]['registryId'] == registry_id
                        break
        
        assert found_rule, "Replication rule not found"

        # Update replication configuration
        updates = {
            "spec": {
                "replicationConfiguration": {
                    "rules": [{
                        "destinations": [{
                            "region": "us-east-2",
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
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Check updated replication configuration
        replication_config = self.get_replication_configuration(ecr_client)
        assert replication_config is not None
        
        # Find updated rule
        found_updated_rule = False
        for rule in replication_config['rules']:
            if 'repositoryFilters' in rule:
                for filter in rule['repositoryFilters']:
                    if filter.get('filter') == repository_prefix and filter.get('filterType') == 'PREFIX_MATCH':
                        found_updated_rule = True
                        # Check updated destination
                        assert rule['destinations'][0]['region'] == 'us-east-2'
                        break
        
        assert found_updated_rule, "Updated replication rule not found"

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check repository deleted
        repository = self.get_repository(ecr_client, resource_name)
        assert repository is None

    def test_delete_replication_configuration(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)
        registry_id = get_account_id()
        
        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id

        # Create without replication
        resource_data = load_ecr_resource(
            "repository",
            additional_replacements=replacements,
        )
        
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Add replication configuration
        repository_prefix = resource_name[:10]
        updates = {
            "spec": {
                "replicationConfiguration": {
                    "rules": [{
                        "destinations": [{
                            "region": "us-west-2",
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
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Verify replication was added
        replication_config = self.get_replication_configuration(ecr_client)
        assert replication_config is not None
        assert len(replication_config.get('rules', [])) > 0

        # Remove replication configuration
        updates = {
            "spec": {
                "replicationConfiguration": None
            }
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Verify replication was removed
        replication_config = self.get_replication_configuration(ecr_client)
        if replication_config:
            # Check our rule is not there
            for rule in replication_config.get('rules', []):
                if 'repositoryFilters' in rule:
                    for filter in rule['repositoryFilters']:
                        assert filter.get('filter') != repository_prefix

        # Cleanup
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted