import base64
import os
import sys
from enum import Enum

import boto3

from iambic.core.context import ctx
from iambic.core.models import BaseModel
from iambic.core.utils import yaml
from iambic.main import (
    run_apply,
    run_clone_repos,
    run_detect,
    run_git_apply,
    run_import,
    run_plan,
)

CONFIG_PATH = os.path.expanduser("~/.iambic/config.yaml")
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
REPO_BASE_PATH = os.path.expanduser("~/.iambic/repos/")
os.makedirs(os.path.dirname(REPO_BASE_PATH), exist_ok=True)

class LambdaCommand(Enum):
    # `import` is reserved in Python. So we just prepend `run_` to everything.
    run_import = "import" 
    run_plan = "plan"
    run_apply = "apply"
    run_detect = "detect"
    run_git_apply = "git_apply"
    run_clone_git_repos = "clone_git_repos"
    
class LambdaContext(BaseModel):
    command: str


def handler(event, context):
    return run_handler(event, context)

def run_handler(event=None, context=None):
    """
    Default handler for AWS Lambda. It is split out from the actual
    handler so we can also run via IDE run configurations
    """
    if not context:
        context = {"command": "import"}
    lambda_context = LambdaContext(**context)
    if not (iambic_config_path := os.getenv("IAMBIC_CONFIG")):
        raise NotImplementedError("You must set the IAMBIC_CONFIG environment variable")
    if not iambic_config_path.startswith("arn:aws:secretsmanager:"):
        raise NotImplementedError(
            "IAMBIC_CONFIG must be an ARN to a secret in AWS Secrets Manager"
        )
    iambic_assume_role = os.getenv("IAMBIC_CONFIG_ASSUME_ROLE")
    secret_arn = iambic_config_path
    region_name = secret_arn.split(":")[3]
    session = boto3.Session(region_name=region_name)
    if iambic_assume_role:
        sts = session.client("sts")
        role_params = dict(
            RoleArn=iambic_assume_role,
            RoleSessionName="iambic",
        )
        role = sts.assume_role(**role_params)
        session = boto3.Session(
            region_name=region_name,
            aws_access_key_id=role["Credentials"]["AccessKeyId"],
            aws_secret_access_key=role["Credentials"]["SecretAccessKey"],
            aws_session_token=role["Credentials"]["SessionToken"],
        )

    client = session.client(service_name="secretsmanager", region_name=region_name)
    get_secret_value_response = client.get_secret_value(SecretId=secret_arn)
    config_text = get_secret_value_response["SecretString"] if "SecretString" in get_secret_value_response else base64.b64decode(get_secret_value_response["SecretBinary"])
    config = yaml.load(config_text)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f)
    match lambda_context.command:
        case LambdaCommand.run_import.value:
            return run_import(
                (CONFIG_PATH,), 
                REPO_BASE_PATH)
        case LambdaCommand.run_plan.value:
            return run_plan(
                CONFIG_PATH, 
                None,
                REPO_BASE_PATH)
        case LambdaCommand.run_apply.value:
            return run_apply(
                True,
                (CONFIG_PATH,), 
                None,
                REPO_BASE_PATH)
        case LambdaCommand.run_detect.value:
            return run_detect(
                CONFIG_PATH,)
        case LambdaCommand.run_git_apply.value:
            return run_git_apply(
                CONFIG_PATH, 
                REPO_BASE_PATH)
        case LambdaCommand.run_clone_git_repos.value:
            return run_clone_repos(
                CONFIG_PATH, 
                REPO_BASE_PATH)
        case _:
            raise NotImplementedError(f"Unknown command {lambda_context.command}")
    

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise Exception("You must pass a command")
    command = sys.argv[1]
    run_handler(None, {"command": command})