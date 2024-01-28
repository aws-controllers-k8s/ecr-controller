# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Fixtures common to all ECR controller tests"""

import dataclasses

from acktest.k8s import resource as k8s
import pytest
from typing import Dict
import os
import distutils.util as util
from typing import Dict
from kubernetes import config, client
from kubernetes.client.api_client import ApiClient

@dataclasses.dataclass
class ConfigMap:
    namespace: str
    name: str
    data: Dict[str, int]

def create_config_map(namespace: str,
                      name: str,
                      data: dict,
                      ):
    _api_client = _get_k8s_api_client()
    body = client.V1Secret()
    body.api_version = 'v1'
    body.data = data
    body.kind = 'ConfigMap'
    body.metadata = {
        'name': name,
    }
    body = _api_client.sanitize_for_serialization(body)
    client.CoreV1Api(_api_client).create_namespaced_config_map(namespace, body)

def _get_k8s_api_client() -> ApiClient:
    # Create new client every time to avoid token refresh issues
    # https://github.com/kubernetes-client/python/issues/741
    # https://github.com/kubernetes-client/python-base/issues/125
    if bool(util.strtobool(os.environ.get('LOAD_IN_CLUSTER_KUBECONFIG', 'false'))):
        config.load_incluster_config()
        return ApiClient()
    return config.new_client_from_config()