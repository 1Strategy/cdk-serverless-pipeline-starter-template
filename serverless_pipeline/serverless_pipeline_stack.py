import os
from aws_cdk import (
    core,
    aws_s3 as s3,
    aws_ssm as ssm,
    aws_codebuild as build,
    aws_codepipeline as pipeline,
    aws_codepipeline_actions as actions,
    aws_cloudformation as cfn,
)


class ServerlessPipelineStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        notification_email = ssm.StringParameter.value_from_lookup(
            self,
            parameter_name='/serverless-pipeline/sns/notifications/primary-email'
        )

        github_user = ssm.StringParameter.value_from_lookup(
            self,
            parameter_name='/serverless-pipeline/codepipeline/github/user'
        )

        github_repo = ssm.StringParameter.value_from_lookup(
            self,
            parameter_name='/serverless-pipeline/codepipeline/github/repo'
        )

        github_token = core.SecretValue.secrets_manager(
            '/serverless-pipeline/secrets/github/token',
            json_field='github-token',
        )

        artifact_bucket = s3.Bucket(
            self, 'BuildArtifactsBucket',
            removal_policy=core.RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.KMS_MANAGED,
            versioned=True,
        )

        build_project = build.PipelineProject(
            self, 'BuildProject',
            project_name='serveless-pipeline',
            description='Build project for the serverless-pipeline',
            environment=build.LinuxBuildImage.STANDARD_2_0,
            environment_variables={
                'BUILD_ARTIFACT_BUCKET': build.BuildEnvironmentVariable(value=artifact_bucket.bucket_name),
            },
            cache=build.Cache.bucket(artifact_bucket, prefix='codebuild-cache'),
            build_spec=build.BuildSpec.from_object({
                'version': '0.2',
                ''
                'phases': {
                    'install': {
                        'runtime-versions': {
                            'nodejs': 10,
                        },
                        'commands': [
                            'echo "--------INSTALL PHASE--------"',
                            'pip3 install aws-sam-cli',
                        ]
                    },
                    'pre_build': {
                        'commands': [
                            'echo "--------PREBUILD PHASE--------"',
                            '# Example shows installation of NPM dependencies for shared deps (layers) in a SAM App',
                            '# cd functions/dependencies/shared_deps_one/nodejs',
                            '# npm install && cd',
                            '# cd functions/dependencies/shared_deps_two/nodejs',
                            '# npm install && cd',
                        ]
                    },
                    'build': {
                        'commands': [
                            'echo "--------BUILD PHASE--------"',
                            'echo "Starting SAM packaging `date` in `pwd`"',
                            'sam package --template-file template.yaml --s3-bucket $BUILD_ARTIFACT_BUCKET --output-template-file packaged.yaml',
                        ]
                    },
                    'post_build': {
                        'commands': [
                            'echo "--------POST-BUILD PHASE--------"',
                            'echo "SAM packaging completed on `date`"',
                        ]
                    }
                },
                'artifacts': {
                    'files': ['packaged.yaml'],
                    'discard-paths': 'yes',
                },
                'cache': {
                    'paths': ['/root/.cache/pip'],
                }
            })
        )

        serverless_pipeline = pipeline.Pipeline(
            self, 'ServerlessPipeline',
            artifact_bucket=artifact_bucket,
            pipeline_name='serverless-pipeline',
            restart_execution_on_update=True,
        )

        source_output = pipeline.Artifact()
        build_output = pipeline.Artifact()
        cfn_output = pipeline.Artifact()

        # NOTE: This Stage/Action requires a manual OAuth handshake in the browser be complete before automated deployment can occur
        # Create a new Pipeline in the console, manually authorize GitHub as a source, and then cancel the pipeline wizard.
        serverless_pipeline.add_stage(stage_name='Source', actions=[
            actions.GitHubSourceAction(
                action_name='SourceCodeRepo',
                owner=github_user,
                oauth_token=github_token,
                repo=github_repo,
                branch='master',
                output=source_output,
            )
        ])
        serverless_pipeline.add_stage(stage_name='Build', actions=[
            actions.CodeBuildAction(
                action_name='CodeBuildProject',
                input=source_output,
                outputs=[build_output],
                project=build_project,
                type=actions.CodeBuildActionType.BUILD,
            )
        ])
        serverless_pipeline.add_stage(stage_name='Staging', actions=[
            actions.CloudFormationCreateReplaceChangeSetAction(
                action_name='CreateChangeSet',
                admin_permissions=True,
                change_set_name='serverless-pipeline-changeset-Staging',
                stack_name='ServerlessPipelineStaging',
                template_path=pipeline.ArtifactPath(
                    build_output,
                    file_name='packaged.yaml'
                ),
                capabilities=[cfn.CloudFormationCapabilities.ANONYMOUS_IAM],
                run_order=1,
            ),
            actions.CloudFormationExecuteChangeSetAction(
                action_name='ExecuteChangeSet',
                change_set_name='serverless-pipeline-changeset-Staging',
                stack_name='ServerlessPipelineStaging',
                output=cfn_output,
                run_order=2,
            ),
        ])

        serverless_pipeline.add_stage(stage_name='Production', actions=[
            actions.CloudFormationCreateReplaceChangeSetAction(
                action_name='CreateChangeSet',
                admin_permissions=True,
                change_set_name='serverless-pipeline-changeset-Production',
                stack_name='ServerlessPipelineProduction',
                template_path=pipeline.ArtifactPath(
                    build_output,
                    file_name='packaged.yaml'
                ),
                capabilities=[cfn.CloudFormationCapabilities.ANONYMOUS_IAM],
                run_order=1,
            ),
            actions.ManualApprovalAction(
                action_name='DeploymentApproval',
                notify_emails=[notification_email],
                run_order=2,
            ),
            actions.CloudFormationExecuteChangeSetAction(
                action_name='ExecuteChangeSet',
                change_set_name='serverless-pipeline-changeset-Production',
                stack_name='ServerlessPipelineProduction',
                output=cfn_output,
                run_order=3,
            ),
        ])

        core.CfnOutput(
            self, 'BuildArtifactsBucketOutput',
            value=artifact_bucket.bucket_name,
            description='Amazon S3 Bucket for Pipeline and Build artifacts',
        )
        core.CfnOutput(
            self, 'CodeBuildProjectOutput',
            value=build_project.project_arn,
            description='CodeBuild Project name',
        )
        core.CfnOutput(
            self, 'CodePipelineOutput',
            value=serverless_pipeline.pipeline_arn,
            description='AWS CodePipeline pipeline name',
        )
