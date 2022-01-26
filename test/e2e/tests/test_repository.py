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

"""Integration tests for the ECR Repository API.
"""

import boto3
import pytest
import time
import logging
from typing import Dict, Tuple

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import TestBootstrapResources, get_bootstrap_resources

RESOURCE_PLURAL = "repositories"

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE = '{"rules":[{"rulePriority":1,"description":"Expire images older than 14 days","selection":'\
    '{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":14},"action":{"type":"expire"}}]}'

@pytest.fixture(scope="module")
def ecr_client():
    return boto3.client("ecr")

@service_marker
@pytest.mark.canary
class TestRepository:

    def get_repository(self, ecr_client, repository_name: str) -> dict:
        try:
            resp = ecr_client.describe_repositories(
                repositoryNames=[repository_name]
            )
        except Exception as e:
            logging.debug(e)
            return None

        
        repositories = resp["repositories"]
        for repository in repositories:
            if repository["repositoryName"] == repository_name:
                return repository

        return None

    def get_lifecycle_policy(self, ecr_client, repository_name: str, registry_id: str) -> str:
        try:
            resp = ecr_client.get_lifecycle_policy(
                repositoryName=repository_name,
                registryId=registry_id,
            )
            return resp['lifecyclePolicyText']
        except Exception as e:
            logging.debug(e)
            return ""

    def get_resource_tags(self, ecr_client, resource_arn: str):
        try:
            resp = ecr_client.list_tags_for_resource(
                resourceArn=resource_arn
            )
            return resp['tags']
        except Exception as e:
            logging.debug(e)
            return None

    def repository_exists(self, ecr_client, repository_name: str) -> bool:
        return self.get_repository(ecr_client, repository_name) is not None

    def test_basic_repository(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository",
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

        # Check ECR repository exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert exists

        # Update CR
        cr["spec"]["imageScanningConfiguration"]["scanOnPush"] = True

        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Check repository scanOnPush scanning configuration
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None
        assert repo["imageScanningConfiguration"]["scanOnPush"] is True

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists

    def test_repository_lifecycle_policy(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository_lifecycle_policy",
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

        # Check ECR repository exists
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        # Check ECR repository lifecycle policy exists
        lifecycle_policy = self.get_lifecycle_policy(ecr_client, resource_name, repo["registryId"])
        assert lifecycle_policy == LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE

        # Remove lifecycle policy
        cr["spec"]["lifecyclePolicy"] = ""

        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        lifecycle_policy = self.get_lifecycle_policy(ecr_client, resource_name, repo["registryId"])
        assert lifecycle_policy == ""

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists

    def test_repository_tags(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository",
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

        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR repository exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert exists

        # Add respository tags
        tags = [
            {
                "key": "k1",
                "value": "v1",
            },
            {
                "key": "k2",
                "value": "v2",
            },
        ]
        cr["spec"]["tags"] = tags

        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        repository_tags = self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"])
        assert len(repository_tags) == len(tags)
        assert repository_tags[0]['Key'] == tags[0]['key']
        assert repository_tags[0]['Value'] == tags[0]['value']
        assert repository_tags[1]['Key'] == tags[1]['key']
        assert repository_tags[1]['Value'] == tags[1]['value']

        
        # Update repository tags
        tags = [
            {
                "key": "k1",
                "value": "v1",
            },
            {
                "key": "k2",
                "value": "v2.updated",
            },
        ]

        cr = k8s.wait_resource_consumed_by_controller(ref)
        cr["spec"]["tags"] = tags
        k8s.patch_custom_resource(ref, cr)

        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        repository_tags = self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"])
        assert len(repository_tags) == len(tags)
        assert repository_tags[0]['Key'] == tags[0]['key']
        assert repository_tags[0]['Value'] == tags[0]['value']
        assert repository_tags[1]['Key'] == tags[1]['key']
        assert repository_tags[1]['Value'] == tags[1]['value']

        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Delete one repository tag
        cr["spec"]["tags"] = tags[:-1]
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        repository_tags = self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"])
        assert len(repository_tags) == len(tags[:-1])
        assert repository_tags[0]['Key'] == tags[0]['key']
        assert repository_tags[0]['Value'] == tags[0]['value']

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists