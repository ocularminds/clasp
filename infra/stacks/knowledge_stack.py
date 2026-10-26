"""Knowledge: a Bedrock Knowledge Base holding the compensation and dispute policy.

Policy documents live in a private S3 bucket under policy/; embeddings (Titan Text Embeddings v2, 1024 dims)
live in an S3 Vectors index, so there is no search cluster to run. `make kb-sync` renders the policy document
from policy.yaml, uploads it with the FAQ and runs an ingestion job. The Knowledge Base id is published to SSM
(/clasp/<stage>/knowledge_base_id) so the tools Lambda finds it without a cross-stack reference."""
from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3vectors as s3vectors
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from ..common import output, stage

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024


class KnowledgeStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs):
        super().__init__(scope, id_, **kwargs)
        self.docs = s3.Bucket(
            self, "PolicyDocs",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
            versioned=True, lifecycle_rules=[s3.LifecycleRule(noncurrent_version_expiration=Duration.days(30))],
            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
        )
        output(self, "POLICY_DOCS_BUCKET", self.docs.bucket_name)

        vectors = s3vectors.CfnVectorBucket(self, "PolicyVectors", vector_bucket_name=f"clasp-{stage()}-policy-vectors-{self.account}")
        index = s3vectors.CfnIndex(
            self, "PolicyIndex", vector_bucket_name=vectors.vector_bucket_name, index_name="policy",
            data_type="float32", dimension=EMBEDDING_DIMENSIONS, distance_metric="cosine",
            # Bedrock stores the chunk text and its metadata on each vector; they must be non-filterable.
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]),
        )
        index.add_dependency(vectors)

        embedding_arn = self.format_arn(service="bedrock", account="", resource="foundation-model", resource_name=EMBEDDING_MODEL)
        role = iam.Role(
            self, "KnowledgeBaseRole", role_name=f"clasp-{stage()}-knowledge-base",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {"aws:SourceArn": self.format_arn(service="bedrock", resource="knowledge-base", resource_name="*")}}),
        )
        role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[embedding_arn]))
        self.docs.grant_read(role)
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3vectors:GetIndex", "s3vectors:QueryVectors", "s3vectors:PutVectors", "s3vectors:GetVectors",
                     "s3vectors:DeleteVectors", "s3vectors:ListVectors", "s3vectors:GetVectorBucket"],
            resources=[vectors.attr_vector_bucket_arn, index.attr_index_arn]))

        self.knowledge_base = bedrock.CfnKnowledgeBase(
            self, "PolicyKnowledgeBase", name=f"clasp-{stage()}-policy", role_arn=role.role_arn,
            description="Clasp compensation and dispute policy: what the assistant may offer and why decisions happen",
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=embedding_arn,
                    embedding_model_configuration=bedrock.CfnKnowledgeBase.EmbeddingModelConfigurationProperty(
                        bedrock_embedding_model_configuration=bedrock.CfnKnowledgeBase.BedrockEmbeddingModelConfigurationProperty(
                            dimensions=EMBEDDING_DIMENSIONS, embedding_data_type="FLOAT32")))),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=index.attr_index_arn, vector_bucket_arn=vectors.attr_vector_bucket_arn)),
        )
        self.knowledge_base.add_dependency(index)
        self.knowledge_base.node.add_dependency(role)

        self.data_source = bedrock.CfnDataSource(
            self, "PolicyDataSource", knowledge_base_id=self.knowledge_base.attr_knowledge_base_id, name="policy-docs",
            description="policy/ prefix of the policy documents bucket",
            data_deletion_policy="RETAIN",
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3", s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=self.docs.bucket_arn, inclusion_prefixes=["policy/"])),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300, overlap_percentage=20))),
        )
        ssm.StringParameter(self, "KnowledgeBaseIdParam", parameter_name=f"/clasp/{stage()}/knowledge_base_id",
                            string_value=self.knowledge_base.attr_knowledge_base_id)
        output(self, "KNOWLEDGE_BASE_ID", self.knowledge_base.attr_knowledge_base_id)
        output(self, "KNOWLEDGE_BASE_ARN", self.knowledge_base.attr_knowledge_base_arn)
        output(self, "KNOWLEDGE_DATA_SOURCE_ID", self.data_source.attr_data_source_id)
