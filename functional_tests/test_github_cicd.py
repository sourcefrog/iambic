from __future__ import annotations

import asyncio
import datetime
import os
import shutil
import subprocess
import tempfile
import time

import pytest
from github import Github

from iambic.config.models import Config
from iambic.core.git import clone_git_repo

os.environ["TESTING"] = "true"

github_config = """
extends:
  - key: AWS_SECRETS_MANAGER
    value: arn:aws:secretsmanager:us-west-2:442632209887:secret:dev/github-token-iambic-templates-itest
    assume_role_arn: arn:aws:iam::442632209887:role/IambicSpokeRole
version: '1'

aws:
  organizations:
    - org_id: 'o-8t0mt0ybdd'
      hub_role_arn: 'arn:aws:iam::580605962305:role/IambicHubRole'
      org_name: 'iambic_test_org_account'
      org_account_id: '580605962305'
      identity_center_account:
        region: 'us-east-1'
      account_rules:
        - included_accounts:
            - '*'
          enabled: true
          iambic_managed: read_and_write
      default_rule:
        enabled: true
        iambic_managed: read_and_write
  accounts:
    - account_id: '192455039954'
      account_name: iambic_test_spoke_account_2
      iambic_managed: read_and_write
      org_id: o-8t0mt0ybdd
      spoke_role_arn: arn:aws:iam::192455039954:role/IambicSpokeRole
    - account_id: '333972133479'
      account_name: iambic_test_spoke_account_3
      iambic_managed: read_and_write
      org_id: o-8t0mt0ybdd
      spoke_role_arn: arn:aws:iam::333972133479:role/IambicSpokeRole
    - account_id: '580605962305'
      account_name: iambic_test_org_account
      iambic_managed: read_and_write
      org_id: o-8t0mt0ybdd
      spoke_role_arn: arn:aws:iam::580605962305:role/IambicSpokeRole
    - account_id: '442632209887'
      account_name: iambic_test_spoke_account_1
      iambic_managed: read_and_write
      org_id: o-8t0mt0ybdd
      spoke_role_arn: arn:aws:iam::442632209887:role/IambicSpokeRole
"""

iambic_role_yaml = """template_type: NOQ::AWS::IAM::Role
identifier: iambic_itest_for_github_cicd
included_accounts:
  - dev
properties:
  assume_role_policy_document:
    statement:
      - action: sts:AssumeRole
        effect: Deny
        principal:
          service: ec2.amazonaws.com
    version: '2012-10-17'
  description: {new_description}
  role_name: iambic_itest_for_github_cicd
"""


@pytest.fixture
def filesystem():
    fd, temp_config_filename = tempfile.mkstemp(
        prefix="iambic_test_temp_config_filename"
    )
    temp_templates_directory = tempfile.mkdtemp(
        prefix="iambic_test_temp_templates_directory"
    )

    with open(fd, "w") as temp_file:
        temp_file.write(github_config)

    try:
        yield (temp_config_filename, temp_templates_directory)
    finally:
        try:
            os.close(fd)
            os.unlink(temp_config_filename)
            shutil.rmtree(temp_templates_directory)
        except Exception as e:
            print(e)


@pytest.fixture
def generate_templates_fixture():
    # to override the conftest version to speed up testing
    pass


# Opens a PR on noqdev/iambic-templates-test. The workflow on the repo will
# pull container with "test label". It will then approve the PR and trigger
# the "iambic git-apply" command on the PR. If the flow is successful, the PR
# will be merged and we will check the workflow to be completed state.
def test_github_cicd(filesystem, generate_templates_fixture):

    if not os.environ.get("GITHUB_ACTIONS", None):
        # If we are running locally on developer machine, we need to ensure
        # the payload is packed into a container image for the test templates repo
        # to run against, so this is why we are building the container images on-the-fly
        subprocess.run("make -f Makefile.itest auth_to_ecr", shell=True, check=True)
        subprocess.run(
            "make -f Makefile.itest build_docker_itest", shell=True, check=True
        )
        subprocess.run(
            "make -f Makefile.itest upload_docker_itest", shell=True, check=True
        )

    temp_config_filename, temp_templates_directory = filesystem
    config = Config.load(temp_config_filename)
    asyncio.run(config.setup_aws_accounts())
    github_token = config.secrets["github-token-iambic-templates-itest"]
    github_repo_name = "noqdev/iambic-templates-itest"
    repo_url = f"https://oauth2:{github_token}@github.com/{github_repo_name}.git"
    repo = clone_git_repo(repo_url, temp_templates_directory, None)
    head_sha = repo.head.commit.hexsha
    print(repo)

    repo_config_writer = repo.config_writer()
    repo_config_writer.set_value(
        "user", "name", "Iambic Github Functional Test for Github"
    )
    repo_config_writer.set_value(
        "user", "email", "github-cicd-functional-test@iambic.org"
    )
    repo_config_writer.release()

    utc_obj = datetime.datetime.utcnow()
    date_isoformat = utc_obj.isoformat()
    date_string = date_isoformat.replace(":", "_")
    new_branch = f"itest/github_cicd_run_{date_string}"
    current = repo.create_head(new_branch)
    current.checkout()
    with open(f"{temp_templates_directory}/last_touched.md", "wb") as f:
        f.write(date_string.encode("utf-8"))
    test_role_path = os.path.join(
        temp_templates_directory,
        "resources/aws/roles/dev/iambic_itest_github_cicd.yaml",
    )

    with open(test_role_path, "w") as temp_role_file:
        utc_obj = datetime.datetime.utcnow()
        date_isoformat = utc_obj.isoformat()
        date_string = date_isoformat.replace(":", "_")
        temp_role_file.write(iambic_role_yaml.format(new_description=date_string))

    if repo.index.diff(None) or repo.untracked_files:
        repo.git.add(A=True)
        repo.git.commit(m="msg")
        repo.git.push("--set-upstream", "origin", current)
        print("git push")

    github_client = Github(github_token)
    github_repo = github_client.get_repo(github_repo_name)
    pull_request_body = "itest"
    pr = github_repo.create_pull(
        title="itest", body=pull_request_body, head=new_branch, base="main"
    )
    pr_number = pr.number

    # test git-plan
    is_workflow_run_successful = False
    datetime_since_comment_issued = datetime.datetime.utcnow()
    while (datetime.datetime.utcnow() - datetime_since_comment_issued).seconds < 120:
        runs = github_repo.get_workflow_runs(event="pull_request", branch=new_branch)
        runs = [run for run in runs if run.created_at >= utc_obj]
        runs = [run for run in runs if run.conclusion == "success"]
        if len(runs) != 1:
            time.sleep(10)
            print("sleeping")
            continue
        else:
            is_workflow_run_successful = True
            break

    assert is_workflow_run_successful

    # let github action to handle the approval flow
    pr.create_issue_comment("approve")
    is_workflow_run_successful = False
    datetime_since_comment_issued = datetime.datetime.utcnow()
    while (datetime.datetime.utcnow() - datetime_since_comment_issued).seconds < 120:
        review_list = github_repo.get_pull(pr_number).get_reviews()
        approved_review_list = [
            review for review in review_list if review.state == "APPROVED"
        ]
        if len(approved_review_list) != 1:
            time.sleep(10)
            print("sleeping")
            continue
        else:
            is_workflow_run_successful = True
            break

    # test iambic git-apply
    pr.create_issue_comment("iambic git-apply")
    is_workflow_run_successful = False
    datetime_since_comment_issued = datetime.datetime.utcnow()
    while (datetime.datetime.utcnow() - datetime_since_comment_issued).seconds < 120:
        runs = github_repo.get_workflow_runs(event="issue_comment", branch="main")
        runs = [run for run in runs if run.created_at >= utc_obj]
        runs = [run for run in runs if run.head_sha == head_sha]
        runs = [run for run in runs if run.conclusion == "success"]
        if len(runs) != 2:
            time.sleep(10)
            print("sleeping")
            continue
        else:
            is_workflow_run_successful = True
            break

    assert is_workflow_run_successful
    check_pull_request = github_repo.get_pull(pr_number)
    assert check_pull_request.merged
