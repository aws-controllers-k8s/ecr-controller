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

"""Integration tests for ECR Cross Account Resource Management.
Ideally we want these tests to be in the ACK runtime, but we don't have a way
to run them there yet. So we'll run them here for now.
"""

import pytest
import time
import logging
import boto3
import os

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_ecr_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.fixtures import create_config_map

RESOURCE_PLURAL = "repositories"

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

TESTING_NAMESPACE = "carm-testing"
ACK_SYSTEM_NAMESPACE = "ack-system"
TESTING_ACCOUNT = "637423602339"
TESTTING_ASSUME_ROLE = "arn:aws:iam::637423602339:role/ack-carm-role-DO-NOT-DELETE"

@service_marker
@pytest.mark.canary
class TestCARM:
    def get_repository(self, repository_name: str) -> dict:
        ecr_client = boto3.client(
            "ecr",
            aws_access_key_id=os.environ["CARM_AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["CARM_AWS_SECRET_ACCESS_KEY"],
            aws_session_token=os.environ["CARM_AWS_SESSION_TOKEN"],
        )
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

    def repository_exists(self, repository_name: str) -> bool:
        return self.get_repository(repository_name) is not None

    def test_basic_repository(self):
        create_config_map(
            ACK_SYSTEM_NAMESPACE,
            "ack-role-account-map",
            {
                TESTING_ACCOUNT: TESTTING_ASSUME_ROLE,
            },
        )
        k8s.create_k8s_namespace(
            TESTING_NAMESPACE,
            annotations={
                "services.k8s.aws/owner-account-id": TESTING_ACCOUNT,
            }
        )

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        resource_name = random_suffix_name("ecr-carm-repository", 24)

        replacements = REPLACEMENT_VALUES.copy()
        replacements["REPOSITORY_NAME"] = resource_name
        replacements["NAMESPACE"] = TESTING_NAMESPACE
        replacements["REGISTRY_ID"] = '"'+TESTING_ACCOUNT +'"'
        # Load ECR CR
        resource_data = load_ecr_resource(
            "repository_carm",
            additional_replacements=replacements,
        )
        logging.debug(resource_data)

        # Create k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace=TESTING_NAMESPACE,
        )

        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)
        assert cr is not None
        assert k8s.get_resource_exists(ref)

        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Check ECR repository exists
        exists = self.repository_exists(resource_name)
        assert exists

        # Delete k8s resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted is True

        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Check ECR repository doesn't exists
        exists = self.repository_exists(resource_name)
        assert not exists