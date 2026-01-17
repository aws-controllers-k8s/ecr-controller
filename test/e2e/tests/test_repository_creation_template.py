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

"""Integration tests for the ECR Repository Creation Template API.
"""

import pytest
import time
import logging
from typing import Dict

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from acktest.k8s import condition
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = "repositorycreationtemplates"

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10


@service_marker
@pytest.mark.canary
class TestRepositoryCreationTemplate:
    def get_repository_creation_template(self, ecr_client, prefix: str) -> dict:
        """Get a repository creation template by prefix."""
        try:
            resp = ecr_client.describe_repository_creation_templates(
                prefixes=[prefix]
            )
        except Exception as e:
            logging.debug(e)
            return None

        templates = resp.get("repositoryCreationTemplates", [])
        for template in templates:
            if template["prefix"] == prefix:
                return template
        return None

    def template_exists(self, ecr_client, prefix: str) -> bool:
        """Check if a repository creation template exists."""
        return self.get_repository_creation_template(ecr_client, prefix) is not None

    def test_create_delete(self, ecr_client):
        """Test basic create and delete operations."""
        resource_name = random_suffix_name("rct-test", 24)
        prefix = random_suffix_name("test", 16)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["NAME"] = resource_name
        replacements["PREFIX"] = prefix

        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository_creation_template",
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
        assert k8s.wait_on_condition(ref, condition.CONDITION_TYPE_RESOURCE_SYNCED, "True", wait_periods=5)

        # Check template exists in AWS
        assert self.template_exists(ecr_client, prefix)

        template = self.get_repository_creation_template(ecr_client, prefix)
        assert template is not None
        assert template["prefix"] == prefix
        assert "CREATE_ON_PUSH" in template["appliedFor"]

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check template deleted from AWS
        assert not self.template_exists(ecr_client, prefix)

    def test_update(self, ecr_client):
        """Test update operations."""
        resource_name = random_suffix_name("rct-update", 24)
        prefix = random_suffix_name("test", 16)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["NAME"] = resource_name
        replacements["PREFIX"] = prefix

        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository_creation_template",
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
        assert k8s.wait_on_condition(ref, condition.CONDITION_TYPE_RESOURCE_SYNCED, "True", wait_periods=5)

        # Check template exists in AWS
        assert self.template_exists(ecr_client, prefix)

        # Update the description
        cr = k8s.get_resource(ref)
        cr["spec"]["description"] = "Updated description for testing"

        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, condition.CONDITION_TYPE_RESOURCE_SYNCED, "True", wait_periods=5)

        # Verify update in AWS
        template = self.get_repository_creation_template(ecr_client, prefix)
        assert template is not None
        assert template["description"] == "Updated description for testing"

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check template deleted from AWS
        assert not self.template_exists(ecr_client, prefix)
