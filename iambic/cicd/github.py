from __future__ import annotations

import json
import os
import shutil
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlparse

import yaml
from github import Github, PullRequest

from iambic.core.git import Repo, clone_git_repo
from iambic.core.logger import log

iambic_app = __import__("iambic.lambda.app", globals(), locals(), [], 0)
lambda_run_handler = getattr(iambic_app, "lambda").app.run_handler
lambda_repo_path = getattr(iambic_app, "lambda").app.REPO_BASE_PATH


MERGEABLE_STATE_CLEAN = "clean"
MERGEABLE_STATE_BLOCKED = "blocked"


SHARED_CONTAINER_GITHUB_DIRECTORY = "/root/data"


class HandleIssueCommentReturnCode(Enum):
    UNDEFINED = 1
    NO_MATCHING_BODY = 2
    MERGEABLE_STATE_NOT_CLEAN = 3
    MERGED = 4


# context is a dictionary structure published by Github Action
# https://docs.github.com/en/actions/learn-github-actions/contexts#github-context
def run_handler(context: dict[str, Any]):
    github_token: str = context["token"]
    event_name: str = context["event_name"]
    log_params = {"event_name": event_name}
    log.info("no op", **log_params)
    github_client = Github(github_token)
    # TODO Support Github Enterprise with custom hostname
    # g = Github(base_url="https://{hostname}/api/v3", login_or_token="access_token")

    f: Callable[[Github, dict[str, Any]]] = EVENT_DISPATCH_MAP.get(event_name)
    if f:
        f(github_client, context)
    else:
        log.error("no supported handler")
        raise Exception("no supported handler")


def format_github_url(repository_url: str, github_token: str) -> str:
    parse_result = urlparse(repository_url)
    return parse_result._replace(
        netloc="oauth2:{0}@{1}".format(github_token, parse_result.netloc)
    ).geturl()


def prepare_local_repo(
    repo_url: str, repo_path: str, pull_request_branch_name: str
) -> Repo:
    if len(os.listdir(repo_path)) > 0:
        raise Exception(f"{repo_path} already exists. This is unexpected.")
    cloned_repo = clone_git_repo(repo_url, repo_path, None)
    for remote in cloned_repo.remotes:
        remote.fetch()
    cloned_repo.git.checkout("-b", "attempt/git-apply")
    cloned_repo.git.merge(f"origin/{pull_request_branch_name}")
    log_params = {"last_commit_message": cloned_repo.head.commit.message}
    log.info(cloned_repo.head.commit.message, **log_params)
    return cloned_repo


def prepare_local_repo_from_remote_branch(
    repo_url: str, repo_path: str, pull_request_branch_name: str
) -> Repo:
    if len(os.listdir(repo_path)) > 0:
        raise Exception(f"{repo_path} already exists. This is unexpected.")
    cloned_repo = clone_git_repo(repo_url, repo_path, pull_request_branch_name)
    log_params = {"last_commit_message": cloned_repo.head.commit.message}
    log.info(cloned_repo.head.commit.message, **log_params)
    return cloned_repo


def load_proposed_changes(filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r") as f:
        return yaml.load(f)


# TODO do more formatting to emphasize resources deletion
def format_proposed_changes(changes: dict) -> str:
    pass


TRUNCATED_WARNING = (
    "Plan is too large and truncated. View Run link to check the entire plan."
)
BODY_MAX_LENGTH = 65000
TRUNCATED_BODY_MAX_LENGTH = BODY_MAX_LENGTH - len(TRUNCATED_WARNING)
GIT_APPLY_COMMENT_TEMPLATE = """iambic {iambic_op} ran with:

```yaml
{plan}
```

<a href="{run_url}">Run</a>
"""


def ensure_body_length_fits_github_spec(body: str) -> str:
    if len(body) > BODY_MAX_LENGTH:
        body = TRUNCATED_WARNING + body[-TRUNCATED_BODY_MAX_LENGTH:-1]
    return body


def post_result_as_pr_comment(
    pull_request: PullRequest, context: dict[str, Any], iambic_op: str
) -> None:
    run_url = (
        context["server_url"]
        + "/"
        + context["repository"]
        + "/actions/runs/"
        + context["run_id"]
        + "/attempts/"
        + context["run_attempt"]
    )
    lines = []
    cwd = os.getcwd()
    filepath = f"{cwd}/proposed_changes.yaml"
    if os.path.exists(filepath):
        with open(filepath) as f:
            lines = f.readlines()
    plan = "".join(lines) if lines else "no changes"
    body = GIT_APPLY_COMMENT_TEMPLATE.format(
        plan=plan, run_url=run_url, iambic_op=iambic_op
    )
    body = ensure_body_length_fits_github_spec(body)
    pull_request.create_issue_comment(body)


def copy_data_to_data_directory() -> None:
    cwd = os.getcwd()
    filepath = f"{cwd}/proposed_changes.yaml"
    dest_dir = SHARED_CONTAINER_GITHUB_DIRECTORY
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    if os.path.exists(filepath):
        shutil.copy(filepath, f"{dest_dir}/proposed_changes.yaml")


def handle_issue_comment(
    github_client: Github, context: dict[str, Any]
) -> HandleIssueCommentReturnCode:

    comment_body = context["event"]["comment"]["body"]
    if comment_body != "iambic git-apply":
        log_params = {"comment_body": comment_body}
        log.info("no op", **log_params)
        return HandleIssueCommentReturnCode.NO_MATCHING_BODY

    github_token = context["token"]
    # repo_name is already in the format {repo_owner}/{repo_short_name}
    repo_name = context["repository"]
    pull_number = context["event"]["issue"]["number"]
    repository_url = context["event"]["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)
    pull_request_branch_name = pull_request.head.ref
    log_params = {"pull_request_branch_name": pull_request_branch_name}
    log.info("PR remote branch name", **log_params)

    if pull_request.mergeable_state != MERGEABLE_STATE_CLEAN:
        # TODO log error and also make a comment to PR
        pull_request.create_issue_comment(
            "mergeable_state is {0}".format(pull_request.mergeable_state)
        )
        return HandleIssueCommentReturnCode.MERGEABLE_STATE_NOT_CLEAN
    try:
        prepare_local_repo(repo_url, lambda_repo_path, pull_request_branch_name)
        lambda_run_handler(None, {"command": "git_apply"})
        post_result_as_pr_comment(pull_request, context, "git-apply")
        copy_data_to_data_directory()
        pull_request.merge()
        return HandleIssueCommentReturnCode.MERGED
    except Exception as e:
        log.error("fault", exception=str(e))
        pull_request.create_issue_comment(
            "exception during git-apply is {0} \n {1}".format(
                pull_request.mergeable_state, e
            )
        )
        raise e


def handle_pull_request(github_client: Github, context: dict[str, Any]) -> None:
    github_token = context["token"]
    # repo_name is already in the format {repo_owner}/{repo_short_name}
    repo_name = context["repository"]
    pull_number = context["event"]["pull_request"]["number"]
    repository_url = context["event"]["repository"]["clone_url"]
    repo_url = format_github_url(repository_url, github_token)
    # repository_url_token
    templates_repo = github_client.get_repo(repo_name)
    pull_request = templates_repo.get_pull(pull_number)
    pull_request_branch_name = pull_request.head.ref
    log_params = {"pull_request_branch_name": pull_request_branch_name}
    log.info("PR remote branch name", **log_params)

    try:
        prepare_local_repo_from_remote_branch(
            repo_url, lambda_repo_path, pull_request_branch_name
        )
        lambda_run_handler(None, {"command": "git_plan"})
        post_result_as_pr_comment(pull_request, context, "git-plan")
        copy_data_to_data_directory()
    except Exception as e:
        log.error("fault", exception=str(e))
        pull_request.create_issue_comment(
            "exception during git-apply is {0} \n {1}".format(
                pull_request.mergeable_state, e
            )
        )
        raise e


EVENT_DISPATCH_MAP: dict[str, Callable] = {
    "issue_comment": handle_issue_comment,
    "pull_request": handle_pull_request,
}


if __name__ == "__main__":
    github_context_json_str = os.environ.get("GITHUB_CONTEXT")
    with open("/root/github_context/github_context.json", "r") as f:
        github_context = json.load(f)
    run_handler(github_context)