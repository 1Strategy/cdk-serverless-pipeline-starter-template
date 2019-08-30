#!/usr/bin/env python3

from aws_cdk import core
import os

from serverless_pipeline.serverless_pipeline_stack import ServerlessPipelineStack


app = core.App()
ServerlessPipelineStack(app, "serverless-pipeline", env=core.Environment(
    account=os.getenv('AWS_CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('AWS_CDK_DEFAULT_REGION'),
))

app.synth()
