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

import pytest
import time
import logging
from typing import Dict, Tuple

from acktest import tags as tagutil
from acktest.resources import random_suffix_name
from acktest import tags as tags
from acktest.aws.identity import get_region, get_account_id
from acktest.k8s import resource as k8s, condition as ackcondition
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = "repositories"

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE = '{"rules":[{"rulePriority":1,"description":"Expire images older than 14 days","selection":'\
    '{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":14},"action":{"type":"expire"}}]}'

REPOSITORY_POLICY_GET_DOWNLOAD_URL_ALL = '{"Version":"2012-10-17","Statement":[{"Sid":"AllowPull","Effect":"Allow","Principal":"*","Action":"ecr:GetDownloadUrlForLayer"}]}'

IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_DEV = '{"filter": "*.dev","filterType": "WILDCARD"}'
IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_STAGING = '{"filter": "*.staging","filterType": "WILDCARD"}'

def minify_json_string(json_string: str) -> str:
    return json_string.replace("\n", "").replace(" ", "")

@pytest.fixture
def repository(request):
    resource_name = random_suffix_name("ecr-repository", 24)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["REPOSITORY_NAME"] = resource_name
    replacements["DELETION_POLICY"] = "delete"
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

    yield (ref, cr)

    if k8s.get_resource_exists(ref):  
        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

@pytest.fixture
def repository_adopt_or_create(request):
    resource_name = random_suffix_name("ecr-repository", 24)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["REPOSITORY_NAME"] = resource_name
    replacements["DELETION_POLICY"] = "retain"
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
    assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)

    # Delete k8s resource
    _, deleted = k8s.delete_custom_resource(ref)
    assert deleted is True

    resource_data = load_ecr_resource(
        "repository_adopt_or_create",
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
    yield (ref, cr)

    # Delete k8s resource
    _, deleted = k8s.delete_custom_resource(ref)
    assert deleted is True



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

    def get_repository_policy(self, ecr_client, repository_name: str, registry_id: str) -> str:
        try:
            resp = ecr_client.get_repository_policy(
                repositoryName=repository_name,
                registryId=registry_id,
            )
            return resp['policyText']
        except Exception as e:
            logging.debug(e)
            return ""

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

    def test_basic_repository(self, ecr_client, repository):
        (ref, cr) = repository

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)

        assert 'spec' in cr
        assert 'name' in cr['spec']
        resource_name = cr['spec']['name']

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
    
    def test_adopt_or_create_repository(self, ecr_client, repository_adopt_or_create):
        (ref, cr) = repository_adopt_or_create
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)

        assert 'spec' in cr
        assert 'name' in cr['spec']
        resource_name = cr['spec']['name']

        # Check ECR repository exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert exists

        # ensure status fields are populated
        assert 'status' in cr
        assert 'repositoryURI' in cr['status']

        # Ensure we update tags after adoption
        repository_tags = tagutil.clean(self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"]))
        desired_tags = cr['spec']['tags']
        assert repository_tags[0]['Key'] == desired_tags[0]['key']
        assert repository_tags[0]['Value'] == desired_tags[0]['value']

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

        # Get latest repository CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

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

    def test_repository_tags(self, ecr_client, repository):
        (ref, cr) = repository
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)

        assert 'spec' in cr
        assert 'name' in cr['spec']
        resource_name = cr['spec']['name']

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

        repository_tags = tagutil.clean(self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"]))
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

        repository_tags = tagutil.clean(self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"]))
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

        repository_tags = tagutil.clean(self.get_resource_tags(ecr_client, cr["status"]["ackResourceMetadata"]["arn"]))
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

    def test_repository_policy(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["REPOSITORY_POLICY"] = REPOSITORY_POLICY_GET_DOWNLOAD_URL_ALL

        # Load Repository CR
        resource_data = load_ecr_resource(
            "repository_policy",
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

        # Get latest repository CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR repository exists
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        # Check ECR repository policy exists
        policy = self.get_repository_policy(ecr_client, resource_name, repo["registryId"])
        assert minify_json_string(policy) == REPOSITORY_POLICY_GET_DOWNLOAD_URL_ALL

        # Remove repository policy
        cr["spec"]["policy"] = ""

        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        policy = self.get_repository_policy(ecr_client, resource_name, repo["registryId"])
        assert policy == ""

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists

    def test_mutability(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_1"] = IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_DEV
        replacements["IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_2"] = IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_STAGING

        # Load Repository CR
        resource_data = load_ecr_resource(
            "repository_mutability",
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

        # Get latest repository CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR repository exists
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        mutability_type = repo["imageTagMutability"]
        exclusion_filters = repo["imageTagMutabilityExclusionFilters"]
        assert mutability_type == "MUTABLE_WITH_EXCLUSION"
        assert len(exclusion_filters) == 2
        assert exclusion_filters[0]["filterType"] == "WILDCARD"
        assert exclusion_filters[0]["filter"] == "*.dev"
        assert exclusion_filters[1]["filterType"] == "WILDCARD"
        assert exclusion_filters[1]["filter"] == "*.staging"

        # Remove repository policy
        cr["spec"]["imageTagMutability"] = "IMMUTABLE_WITH_EXCLUSION"
        cr["spec"]["imageTagMutabilityExclusionFilters"] = [
            {
                "filterType": "WILDCARD",
                "filter": "*.prod"
            },
            {
                "filterType": "WILDCARD",
                "filter": "*.beta"
            }
        ]


        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        mutability_type = repo["imageTagMutability"]
        exclusion_filters = repo["imageTagMutabilityExclusionFilters"]
        assert mutability_type == "IMMUTABLE_WITH_EXCLUSION"
        assert len(exclusion_filters) == 2
        assert exclusion_filters[0]["filterType"] == "WILDCARD"
        assert exclusion_filters[0]["filter"] == "*.prod"
        assert exclusion_filters[1]["filterType"] == "WILDCARD"
        assert exclusion_filters[1]["filter"] == "*.beta"

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists

    def test_repository_create_will_all_fields(self, ecr_client):
        resource_name = random_suffix_name("ecr-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["REPOSITORY_POLICY"] = REPOSITORY_POLICY_GET_DOWNLOAD_URL_ALL
        replacements["REPOSITORY_LIFECYCLE_POLICY"] = LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE
        replacements["IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_1"] = IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_DEV
        replacements["IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_2"] = IMAGE_TAG_MUTABILITY_EXCLUSION_FILTER_STAGING
        replacements["REGISTRY_ID"] = get_account_id()

        # Load Repository CR
        resource_data = load_ecr_resource(
            "repository_all_fields",
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

        # Get latest repository CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR repository exists
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        # Check ECR repository policy exists
        policy = self.get_repository_policy(ecr_client, resource_name, repo["registryId"])
        assert minify_json_string(policy) == REPOSITORY_POLICY_GET_DOWNLOAD_URL_ALL
        # Check ECR repository lifecycle policy exists
        lifecycle_policy = self.get_lifecycle_policy(ecr_client, resource_name, repo["registryId"])
        assert lifecycle_policy == LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE
        # Check image mutability
        assert repo["imageTagMutability"] == "MUTABLE_WITH_EXCLUSION"
        assert len(repo["imageTagMutabilityExclusionFilters"]) == 2


        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists

    def test_repository_invalid_lifecycle_policy_remains_managed(self, ecr_client):
        """Test that repository remains managed even when lifecycle policy fails to apply.
        
        This test validates the fix for the bug where a failure in setting the lifecycle
        policy during creation would leave the repository unmanaged. The controller should
        now requeue and keep the resource managed, showing an error condition instead of
        a terminal "resource already exists" condition.
        """
        resource_name = random_suffix_name("ecr-repository", 24)

        # Invalid lifecycle policy - rulePriority should be int, not string
        invalid_lifecycle_policy = '{"rules":[{"rulePriority":"invalid","description":"Test","selection":{"tagStatus":"invalid","countType":"imageCountMoreThan","countNumber":"not-a-number"},"action":{"type":"invalid"}}]}'

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["REPOSITORY_LIFECYCLE_POLICY"] = invalid_lifecycle_policy

        # Load Repository CR
        resource_data = load_ecr_resource(
            "repository_invalid_lifecycle_policy",
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

        # Get latest repository CR
        cr = k8s.wait_resource_consumed_by_controller(ref)

        # Check ECR repository exists (it should be created despite lifecycle policy failure)
        repo = self.get_repository(ecr_client, resource_name)
        assert repo is not None

        # Verify the resource is still managed by ACK
        # We expect a terminal condition due to the invalid lifecycle policy,
        # but it should NOT be an "unmanaged" or "already exists" condition
        assert 'status' in cr
        assert 'conditions' in cr['status']
        
        # Check that we don't have an "unmanaged" terminal condition
        conditions = cr['status']['conditions']
        unmanaged_condition = None
        for condition in conditions:
            if condition.get('type') == 'ACK.Terminal':
                message = condition.get('message', '').lower()
                # Check for indicators that the resource is considered unmanaged
                if 'Resource already exists' in message:
                    unmanaged_condition = condition
                    break
        
        # The repository should remain managed (no unmanaged terminal condition)
        assert unmanaged_condition is None, "Repository should remain managed by ACK, not marked as unmanaged"
        
        # Resource should NOT be synced due to the invalid lifecycle policy
        synced = k8s.get_resource_condition(ref, ackcondition.CONDITION_TYPE_RESOURCE_SYNCED)
        assert synced["status"] != "True"

        # The lifecycle policy should not be set due to the invalid value
        lifecycle_policy = self.get_lifecycle_policy(ecr_client, resource_name, repo["registryId"])
        assert lifecycle_policy == ""

        # Now fix the lifecycle policy with a valid one
        valid_lifecycle_policy = LIFECYCLE_POLICY_FILTERING_ON_IMAGE_AGE
        cr["spec"]["lifecyclePolicy"] = valid_lifecycle_policy

        # Patch k8s resource
        k8s.patch_custom_resource(ref, cr)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=5)


        # Verify the lifecycle policy is now set correctly
        lifecycle_policy = self.get_lifecycle_policy(ecr_client, resource_name, repo["registryId"])
        assert lifecycle_policy == valid_lifecycle_policy

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exist
        exists = self.repository_exists(ecr_client, resource_name)
        assert not exists
