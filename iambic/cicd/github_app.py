#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

import boto3
import github
import jwt
import requests
from botocore.exceptions import ClientError

from iambic.cicd.github import (
    HandleIssueCommentReturnCode,
    handle_iambic_git_apply,
    handle_iambic_git_plan,
    iambic_app,
    lambda_repo_path,
    lambda_run_handler,
    prepare_local_repo_for_new_commits,
)
from iambic.core.logger import log
from iambic.main import run_detect, run_expire, run_import

GITHUB_APP_ID = "293178"  # FIXME
GITHUB_APP_PEM_PATH = "/Users/stevenmoy/Downloads/steven-test-github-app.2023-02-13.private-key.pem"  # FIXME
INSTANCE_OF_APP_INSTALLATION = "34179484"  # FIXME

__GITHUB_APP_WEBHOOK_SECRET__ = ""


# FIXME Lambda execution time is at most 15 minutes, and the Github installation token is at most
# 10 min validation period.

# FIXME struct logs is not showing up on lambda cloudwatch logs

# FIXME exception during git-plan is unknown
# /tmp/.iambic/repos/ already exists. This is unexpected.
# This is due to lambda reusing the already running container


def format_github_url(repository_url: str, github_token: str) -> str:
    parse_result = urlparse(repository_url)
    return parse_result._replace(
        netloc="x-access-token:{0}@{1}".format(github_token, parse_result.netloc)
    ).geturl()


def get_static_app_bearer_token() -> str:
    # FIXME app_id
    app_id = GITHUB_APP_ID

    payload = {
        # Issued at time
        "iat": int(time.time()),
        # JWT expiration time (10 minutes maximum)
        "exp": int(time.time()) + 600,
        # GitHub App's identifier
        "iss": app_id,
    }

    # Create JWT
    return jwt.encode(payload, get_app_private_key(), algorithm="RS256")


def get_app_bearer_token(private_key, app_id) -> str:

    payload = {
        # Issued at time
        "iat": int(time.time()),
        # JWT expiration time (10 minutes maximum)
        "exp": int(time.time()) + 600,
        # GitHub App's identifier
        "iss": app_id,
    }

    # Create JWT
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_app_private_key() -> str:
    # Open PEM
    with open(GITHUB_APP_PEM_PATH, "rb") as pem_file:
        signing_key = pem_file.read()
    return signing_key


def list_installations() -> list:
    encoded_jwt = get_app_bearer_token()
    response = requests.get(
        "https://api.github.com/app/installations",
        headers={
            "Accept": "application/vnd.github.v3.text-match+json",
            "Authorization": f"Bearer {encoded_jwt}",
        },
    )
    installations = json.loads(response.text)
    return installations


def get_static_installation_token() -> None:
    encoded_jwt = get_static_app_bearer_token()
    access_tokens_url = "https://api.github.com/app/installations/34179484/access_tokens"  # FIXME constant
    response = requests.post(
        access_tokens_url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {encoded_jwt}",
        },
    )
    payload = json.loads(response.text)
    installation_token = payload["token"]
    return installation_token


def post_pr_comment() -> None:
    # github_client = github.Github(
    #     app_auth=github.AppAuthentication(          # not supported until version 2.0 https://github.com/PyGithub/PyGithub/commits/5e27c10a3140c3b9bbf71a0b71c96e71e1e3496c/github/AppAuthentication.py
    #         app_id=GITHUB_APP_ID,
    #         private_key=get_app_private_key(),
    #         installation_id=INSTANCE_OF_APP_INSTALLATION,
    #         ),
    # )
    # repo_name is already in the format {repo_owner}/{repo_short_name}
    login_or_token = get_static_installation_token()
    github_client = github.Github(login_or_token=login_or_token)
    repo_name = "noqdev/iambic-templates-itest"  # FIXME constants
    pull_number = 248  # FIXME constants
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)
    pull_request_branch_name = pull_request.head.ref
    log_params = {"pull_request_branch_name": pull_request_branch_name}
    log.info("PR remote branch name", **log_params)
    body = "posting as github app"
    pull_request.create_issue_comment(body)


def get_app_private_key_as_lambda_context():
    # assuming we are already in an lambda execution context
    secret_name = "dev/test-github-app-private-key"  # FIXME
    region_name = "us-west-2"  # FIXME

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        raise e

    # Decrypts secret using the associated KMS key.
    return get_secret_value_response["SecretString"]


def get_installation_token(app_id, installation_id):
    encoded_jwt = get_app_bearer_token(get_app_private_key_as_lambda_context(), app_id)
    access_tokens_url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"  # FIXME constant
    response = requests.post(
        access_tokens_url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {encoded_jwt}",
        },
    )
    payload = json.loads(response.text)
    installation_token = payload["token"]
    return installation_token


def run_handler(event=None, context=None):
    """
    Default handler for AWS Lambda. It is split out from the actual
    handler so we can also run via IDE run configurations
    """

    # debug
    print(event)

    github_event = event["headers"]["x-github-event"]
    app_id = event["headers"]["x-github-hook-installation-target-id"]
    # FIXME implement webhooks security secrets
    webhook_payload = json.loads(event["body"])
    installation_id = webhook_payload["installation"]["id"]
    github_override_token = get_installation_token(app_id, installation_id)

    github_client = github.Github(github_override_token)

    f: Callable[[str, github.Github, dict[str, Any]]] = EVENT_DISPATCH_MAP.get(
        github_event
    )
    if f:
        f(github_override_token, github_client, webhook_payload)
    else:
        log.error("no supported handler")
        raise Exception("no supported handler")


def handle_pull_request(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> None:
    # replace with a different github client because we need a different
    # identity to leave the "iambic git-plan". Otherwise, it won't be able
    # to trigger the correct react-to-comment workflow.
    # repo_name is already in the format {repo_owner}/{repo_short_name}
    repo_name = webhook_payload["repository"]["full_name"]
    pull_number = webhook_payload["pull_request"]["number"]
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)
    repository_url = webhook_payload["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)
    pull_request_branch_name = pull_request.head.ref

    return handle_iambic_git_plan(
        None,
        pull_request,
        repo_name,
        pull_number,
        pull_request_branch_name,
        repo_url,
        proposed_changes_path="/tmp/proposed_changes.yaml",
    )


def handle_issue_comment(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> HandleIssueCommentReturnCode:

    comment_body = webhook_payload["comment"]["body"]
    log_params = {"COMMENT_DISPATCH_MAP_KEYS": COMMENT_DISPATCH_MAP.keys()}
    log.info("COMMENT_DISPATCH_MAP keys", **log_params)
    if comment_body not in COMMENT_DISPATCH_MAP:
        log_params = {"comment_body": comment_body}
        log.error("handle_issue_comment: no op", **log_params)
        return HandleIssueCommentReturnCode.NO_MATCHING_BODY

    repo_name = webhook_payload["repository"]["full_name"]
    pull_number = webhook_payload["issue"]["number"]
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)

    # repo_name is already in the format {repo_owner}/{repo_short_name}
    repository_url = webhook_payload["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)
    pull_request_branch_name = pull_request.head.ref
    log_params = {"pull_request_branch_name": pull_request_branch_name}
    log.info("PR remote branch name", **log_params)

    comment_func: Callable = COMMENT_DISPATCH_MAP[comment_body]
    return comment_func(
        None,
        pull_request,
        repo_name,
        pull_number,
        pull_request_branch_name,
        repo_url,
        proposed_changes_path="/tmp/proposed_changes.yaml",
    )


def handle_workflow_run(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> None:

    action = webhook_payload["action"]
    if action != "requested":
        return

    workflow_path = webhook_payload["workflow_run"]["path"]

    if workflow_path not in WORKFLOW_DISPATCH_MAP:
        log_params = {"workflow_path": workflow_path}
        log.error("handle_issue_comment: no op", **log_params)
        return

    workflow_func: Callable = WORKFLOW_DISPATCH_MAP[workflow_path]
    return workflow_func(github_token, github_client, webhook_payload)


def handle_expire(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> None:

    # repo_name is already in the format {repo_owner}/{repo_short_name}
    repository_url = webhook_payload["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)
    try:
        repo = prepare_local_repo_for_new_commits(repo_url, lambda_repo_path, "expire")

        run_expire(None, lambda_repo_path)
        repo.git.add(".")
        diff_list = repo.head.commit.diff()
        if len(diff_list) > 0:
            repo.git.commit("-m", "expire")  # FIXME

            getattr(
                iambic_app, "lambda"
            ).app.PLAN_OUTPUT_PATH = "/tmp/proposed_changes.yaml"
            lambda_run_handler(None, {"command": "git_apply"})

            # if it's in a PR, it's more natural to upload the proposed_changes.yaml to somewhere
            # current implementation, it's just logging to standard out
            lines = []
            filepath = "/tmp/proposed_changes.yaml"
            if os.path.exists(filepath):
                with open(filepath) as f:
                    lines = f.readlines()
            log_params = {"proposed_changes": lines}
            log.info("handle_expire ran", **log_params)

            repo.remotes.origin.push(refspec="HEAD:main").raise_if_error()  # FIXME
        else:
            log.info("handle_expire no changes")
    except Exception as e:
        log.error("fault", exception=str(e))
        raise e


def handle_import(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> None:

    repository_url = webhook_payload["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)

    try:
        repo = prepare_local_repo_for_new_commits(repo_url, lambda_repo_path, "import")

        run_import(lambda_repo_path)
        repo.git.add(".")
        diff_list = repo.head.commit.diff()
        if len(diff_list) > 0:
            repo.git.commit("-m", "import")  # FIXME
            repo.remotes.origin.push(refspec="HEAD:main").raise_if_error()  # FIXME
        else:
            log.info("handle_import no changes")
    except Exception as e:
        log.error("fault", exception=str(e))
        raise e


def handle_detect_changes_from_eventbridge(
    github_token: str, github_client: github.Github, webhook_payload: dict[str, Any]
) -> None:

    repository_url = webhook_payload["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)

    default_branch = "main"
    repo_name = webhook_payload["repository"]["full_name"]
    templates_repo = github_client.get_repo(repo_name)
    default_branch = templates_repo.default_branch

    try:
        repo = prepare_local_repo_for_new_commits(repo_url, lambda_repo_path, "detect")

        run_detect(lambda_repo_path)
        repo.git.add(".")
        diff_list = repo.head.commit.diff()
        if len(diff_list) > 0:
            repo.git.commit("-m", "detect")  # FIXME
            repo.remotes.origin.push(refspec=f"HEAD:{default_branch}").raise_if_error()
        else:
            log.info("handle_detect no changes")
    except Exception as e:
        log.error("fault", exception=str(e))
        raise e


EVENT_DISPATCH_MAP: dict[str, Callable] = {
    "issue_comment": handle_issue_comment,
    "pull_request": handle_pull_request,
    "workflow_run": handle_workflow_run,
}


COMMENT_DISPATCH_MAP: dict[str, Callable] = {
    "iambic git-apply": handle_iambic_git_apply,
    "iambic git-plan": handle_iambic_git_plan,
}

WORKFLOW_DISPATCH_MAP: dict[str, Callable] = {
    ".github/workflows/iambic-expire.yml": handle_expire,
    ".github/workflows/iambic-import.yml": handle_import,
    ".github/workflows/iambic-detect.yml": handle_detect_changes_from_eventbridge,
}


# Use to verify Github App Webhook Secret Using SHA256
def calculate_signature(webhook_secret: str, payload: str) -> str:
    secret_in_bytes = bytes(webhook_secret, "utf-8")
    digest = hmac.new(
        key=secret_in_bytes, msg=payload.encode("utf-8"), digestmod=hashlib.sha256
    )
    signature = digest.hexdigest()
    return signature


def verify_signature(sig: str, payload: str) -> None:
    good_sig = calculate_signature(__GITHUB_APP_WEBHOOK_SECRET__, payload)
    if not hmac.compare_digest(good_sig, sig):
        raise Exception("Bad signature")


if __name__ == "__main__":
    post_pr_comment()