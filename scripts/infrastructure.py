"""Terraform provisioning stage; server configuration and checks are separate."""
import json


def apply(context: dict, runner, validator, plan_path) -> None:
    runner(['plan', '-input=false', '-no-color', '-out=' + str(plan_path)])
    plan = json.loads(runner(['show', '-json', str(plan_path)], True))
    validator(plan, context=context)
    runner(['apply', '-input=false', '-no-color', str(plan_path)])
