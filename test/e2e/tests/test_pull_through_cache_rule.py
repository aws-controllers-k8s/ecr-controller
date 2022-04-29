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

"""Integration tests for the ECR Pull Through Cache Rule API.
"""

import pytest
import time
import logging
from typing import Dict, Tuple

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import BootstrapResources, get_bootstrap_resources

RESOURCE_PLURAL = "pullthroughcacherules"

CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

@service_marker
@pytest.mark.canary
class TestPullThroughCacheRule:
    def get_pull_through_cache_rule(self, ecr_client, registry_id: str, ecr_repository_prefix: str) -> dict:
        try:
            resp = ecr_client.describe_pull_through_cache_rules(
                registryId=registry_id,
                ecrRepositoryPrefixes=[ecr_repository_prefix]
            )
        except Exception as e:
            logging.debug(e)
            return None

        pull_through_cache_rules = resp["pullThroughCacheRules"]
        for rule in pull_through_cache_rules:
            if rule["registryId"] == registry_id and rule["ecrRepositoryPrefix"] == ecr_repository_prefix:
                return rule
        return None

    def get_registry_id(self, ecr_client) -> str:
        try:
            resp = ecr_client.describe_registry()
            return resp["registryId"]
        except Exception as e:
            logging.debug(e)
            return ""


    def pull_through_cache_rule_exists(self, ecr_client, registry_id: str, ecr_repository_prefix:str) -> bool:
        return self.get_pull_through_cache_rule(ecr_client, registry_id, ecr_repository_prefix) is not None

    def test_basic_pull_through_cache_rule(self, ecr_client):
        resource_name = random_suffix_name("ecr-ptcr", 24)
        registry_id = self.get_registry_id(ecr_client)
        ecr_repository_prefix = "ecr-public"

        replacements = REPLACEMENT_VALUES.copy()
        replacements["NAME"] = resource_name
        replacements["REGISTRY_ID"] = registry_id
        replacements["ECR_REPOSITORY_PREFIX"] = ecr_repository_prefix
        replacements["UPSTREAM_REGISTRY_URL"] = "public.ecr.aws"
        # Load ECR CR
        resource_data = load_ecr_resource(
            "pull_through_cache_rule",
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

        # Get latest PTCR CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR PTCR exists
        exists = self.pull_through_cache_rule_exists(ecr_client, registry_id, ecr_repository_prefix)
        assert exists

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR PTCR doesn't exists
        exists = self.pull_through_cache_rule_exists(ecr_client, registry_id, ecr_repository_prefix)
        assert not exists
