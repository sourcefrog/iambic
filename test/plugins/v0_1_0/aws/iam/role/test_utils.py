from __future__ import annotations

import json
from typing import Any, Dict

import boto3
import pytest
from moto import mock_iam

from iambic.core.models import ProposedChangeType
from iambic.plugins.v0_1_0.aws.iam.role.utils import (
    apply_role_inline_policies,
    apply_role_managed_policies,
    apply_role_permission_boundary,
    apply_role_tags,
    delete_iam_role,
    get_role,
    get_role_inline_policies,
    get_role_inline_policy_names,
    get_role_instance_profiles,
    get_role_managed_policies,
    get_role_policy,
    list_role_tags,
    list_roles,
)

EXAMPLE_ROLE_NAME = "example_role_name"
EXAMPLE_ASSUME_ROLE_DOCUMENT = """
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": [
          "elasticmapreduce.amazonaws.com",
          "datapipeline.amazonaws.com"
        ]
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"""
EXAMPLE_INLINE_POLICY_NAME = "example_inline_policy_name"
EXAMPLE_INLINE_POLICY_DOCUMENT = """
{
   "Version":"2012-10-17",
   "Statement":[
      {
         "Effect":"Allow",
         "Action":"acm:ListCertificates",
         "Resource":"*"
      }
   ]
}
"""
EXAMPLE_TAG_KEY = "test_key"
EXAMPLE_TAG_VALUE = "test_value"
EXAMPLE_MANAGED_POLICY_ARN = "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"


class FakeIamClient(object):
    def get_account_authorization_details(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "RoleDetailList": [
                {
                    "RoleName": "test_role",
                    "PermissionsBoundary": {
                        "PermissionsBoundaryType": "PermissionsBoundaryPolicy",
                        "PermissionsBoundaryArn": "string",
                    },
                }
            ],
            "IsTruncated": False,
        }

    def list_roles(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "Roles": [
                {
                    "RoleName": "test_role",
                }
            ],
            "IsTruncated": False,
        }


@pytest.fixture
def iam_client():
    # until we can integration moto library, we are faking some iam methods
    return FakeIamClient()


@pytest.mark.asyncio
async def test_list_roles(iam_client):
    roles = await list_roles(iam_client)
    assert len(roles) > 0


@pytest.fixture
def mock_iam_client():
    with mock_iam():
        iam_client = boto3.client("iam")
        _ = iam_client.create_role(
            RoleName=EXAMPLE_ROLE_NAME,
            AssumeRolePolicyDocument=EXAMPLE_ASSUME_ROLE_DOCUMENT,
            Tags=[
                {
                    "Key": EXAMPLE_TAG_KEY,
                    "Value": EXAMPLE_TAG_VALUE,
                }
            ],
        )
        _ = iam_client.put_role_policy(
            RoleName=EXAMPLE_ROLE_NAME,
            PolicyName=EXAMPLE_INLINE_POLICY_NAME,
            PolicyDocument=EXAMPLE_INLINE_POLICY_DOCUMENT,
        )
        _ = iam_client.attach_role_policy(
            RoleName=EXAMPLE_ROLE_NAME, PolicyArn=EXAMPLE_MANAGED_POLICY_ARN
        )
        yield iam_client


@pytest.mark.asyncio
async def test_get_role_inline_policy_names(mock_iam_client):
    names = await get_role_inline_policy_names(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert names == [EXAMPLE_INLINE_POLICY_NAME]


@pytest.mark.asyncio
async def test_get_role_instance_profiles(mock_iam_client):
    profiles = await get_role_instance_profiles(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert profiles == []


@pytest.mark.asyncio
async def test_list_role_tags(mock_iam_client):
    tags = await list_role_tags(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert tags == [{"Key": EXAMPLE_TAG_KEY, "Value": EXAMPLE_TAG_VALUE}]


@pytest.mark.asyncio
async def test_get_role_policy(mock_iam_client):
    inline_policy = await get_role_policy(
        EXAMPLE_ROLE_NAME, EXAMPLE_INLINE_POLICY_NAME, mock_iam_client
    )
    assert inline_policy is not None


@pytest.mark.asyncio
async def test_get_role_inline_policies(mock_iam_client):
    inline_policies = await get_role_inline_policies(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert len(inline_policies) == 1
    assert EXAMPLE_INLINE_POLICY_NAME in inline_policies.keys()

    inline_policies = await get_role_inline_policies(
        EXAMPLE_ROLE_NAME, mock_iam_client, as_dict=False
    )
    assert len(inline_policies) == 1
    assert inline_policies[0]["PolicyName"] == EXAMPLE_INLINE_POLICY_NAME


@pytest.mark.asyncio
async def test_get_role_managed_policies(mock_iam_client):
    managed_policies = await get_role_managed_policies(
        EXAMPLE_ROLE_NAME, mock_iam_client
    )
    assert managed_policies[0]["PolicyArn"] == EXAMPLE_MANAGED_POLICY_ARN


@pytest.mark.asyncio
async def test_get_role(mock_iam_client):
    role = await get_role(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert role["RoleName"] == EXAMPLE_ROLE_NAME
    assert role["Tags"] == [{"Key": EXAMPLE_TAG_KEY, "Value": EXAMPLE_TAG_VALUE}]

    # Remove the tags from the role and check that this is able to return the Tag dict as empty
    mock_iam_client.untag_role(RoleName=EXAMPLE_ROLE_NAME, TagKeys=[EXAMPLE_TAG_KEY])

    # Describe again:
    role = await get_role(EXAMPLE_ROLE_NAME, mock_iam_client)
    assert role["Tags"] == []


@pytest.mark.asyncio
async def test_apply_role_tags_on_detach(mock_iam_client):
    template_tags = []
    existing_tags = [{"Key": EXAMPLE_TAG_KEY, "Value": EXAMPLE_TAG_VALUE}]
    log_params = {}
    proposed_changes = await apply_role_tags(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_tags,
        existing_tags,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.DETACH


@pytest.mark.asyncio
async def test_apply_role_tags_on_attach(mock_iam_client):
    template_tags = [{"Key": EXAMPLE_TAG_KEY, "Value": EXAMPLE_TAG_VALUE}]
    existing_tags = []
    log_params = {}
    proposed_changes = await apply_role_tags(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_tags,
        existing_tags,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.ATTACH


@pytest.mark.asyncio
async def test_apply_role_managed_policies_on_attach(mock_iam_client):
    template_policies = [{"PolicyArn": EXAMPLE_MANAGED_POLICY_ARN}]
    existing_policies = []
    log_params = {}
    proposed_changes = await apply_role_managed_policies(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_policies,
        existing_policies,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.ATTACH


@pytest.mark.asyncio
async def test_apply_role_managed_policies_on_detach(mock_iam_client):
    template_policies = []
    existing_policies = [{"PolicyArn": EXAMPLE_MANAGED_POLICY_ARN}]
    log_params = {}
    proposed_changes = await apply_role_managed_policies(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_policies,
        existing_policies,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.DETACH


@pytest.mark.asyncio
async def test_apply_role_permission_boundary_on_attach(mock_iam_client):
    template_permission_boundary = {"PolicyArn": EXAMPLE_MANAGED_POLICY_ARN}
    existing_permission_boundary = {}
    log_params = {}
    proposed_changes = await apply_role_permission_boundary(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_permission_boundary,
        existing_permission_boundary,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.ATTACH


@pytest.mark.asyncio
async def test_apply_role_permission_boundary_on_detach(mock_iam_client):
    template_permission_boundary = {}
    existing_permission_boundary = {
        "PermissionsBoundaryArn": EXAMPLE_MANAGED_POLICY_ARN
    }
    log_params = {}
    proposed_changes = await apply_role_permission_boundary(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_permission_boundary,
        existing_permission_boundary,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.DETACH


@pytest.mark.asyncio
async def test_apply_role_inline_policies_on_attach(mock_iam_client):
    template_policies = [{"PolicyName": EXAMPLE_INLINE_POLICY_NAME}]
    template_policies[0].update(json.loads(EXAMPLE_INLINE_POLICY_DOCUMENT))
    existing_policies = []
    log_params = {}
    proposed_changes = await apply_role_inline_policies(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_policies,
        existing_policies,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.CREATE


@pytest.mark.asyncio
async def test_apply_role_inline_policies_on_detach(mock_iam_client):
    template_policies = []
    existing_policies = [{"PolicyName": EXAMPLE_INLINE_POLICY_NAME}]
    existing_policies[0].update(json.loads(EXAMPLE_INLINE_POLICY_DOCUMENT))
    log_params = {}
    proposed_changes = await apply_role_inline_policies(
        EXAMPLE_ROLE_NAME,
        mock_iam_client,
        template_policies,
        existing_policies,
        log_params,
    )
    assert proposed_changes[0].change_type == ProposedChangeType.DELETE


@pytest.mark.asyncio
async def test_delete_iam_role(mock_iam_client):
    log_params = {}
    await delete_iam_role(EXAMPLE_ROLE_NAME, mock_iam_client, log_params)
