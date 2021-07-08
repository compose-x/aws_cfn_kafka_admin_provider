#  -*- coding: utf-8 -*-
# SPDX-License-Identifier: MPL-2.0
# Copyright 2020-2021 John Mille <john@ews-network.net>


"""Main module."""

import re
from copy import deepcopy

import json
import yaml

try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader
from troposphere import Template
from troposphere import Ref, Sub, GetAtt
from troposphere import AWS_NO_VALUE
from cfn_tools import CfnYamlLoader

from aws_custom_ews_kafka_resources import KafkaAclPolicy
from aws_custom_ews_kafka_resources.custom import (
    KafkaTopic as CTopic,
    KafkaAcl as CACLs,
    KafkaTopicSchema as CTopicSchema,
)
from aws_custom_ews_kafka_resources.resource import (
    KafkaTopic as RTopic,
    KafkaAcl as RACLs,
    KafkaTopicSchema as RTopicSchema,
)

from .model import (
    Model,
    EwsKafkaParmeters,
    Topics,
    ACLs,
    SecurityProtocol,
    SASLMechanism,
)
from .model import (
    Policy,
    Action,
    ResourceType,
    PatternType,
    Effect,
    Schema,
    SchemasDef,
    Type as SchemaType,
    SerializeAttribute,
    CompatibilityMode,
    DeletionPolicy,
)

NONALPHANUM = re.compile(r"([^a-zA-Z0-9]+)")


def keyisset(x, y):
    """
    Macro to figure if the the dictionary contains a key and that the key is not empty

    :param x: The key to check presence in the dictionary
    :type x: str
    :param y: The dictionary to check for
    :type y: dict

    :returns: True/False
    :rtype: bool
    """
    if isinstance(y, dict) and x in y.keys() and y[x]:
        return True
    return False


def keypresent(x, y):
    """
    Macro to figure if the the dictionary contains a key and that the key is not empty

    :param x: The key to check presence in the dictionary
    :type x: str
    :param y: The dictionary to check for
    :type y: dict

    :returns: True/False
    :rtype: bool
    """
    if isinstance(y, dict) and x in y.keys():
        return True
    return False


def merge_topics(final, override, extend_all=False):
    """
    Function to override and update settings from override to primary
    Topics are filtered out via the Name property

    :param dict final:
    :param dict override:
    :param extend_all: Whether the policies or ACLs can be merged.
    :return: The final merged dict
    :rtype: dict
    """
    if keyisset("Topics", override):
        override_topics = Topics.parse_obj(override["Topics"]).dict()
        if keypresent("Topics", override_topics) and not extend_all:
            del override_topics["Topics"]
            final["Topics"].update(override_topics)
        elif keyisset("Topics", override_topics) and extend_all:
            if keyisset("Topics", final["Topics"]):
                merged_lists = override_topics["Topics"] + final["Topics"]["Topics"]
            else:
                merged_lists = override_topics["Topics"]

            topics = list({v["Name"]: v for v in merged_lists}.values())
            final["Topics"].update(override_topics)
            final["Topics"]["Topics"] = topics


def merge_acls(final, override, extend_all=False):
    """
    Function to override and update settings from override to primary
    All ACL policy is a dictionary made of simple objects, no key filtering

    :param dict final:
    :param dict override:
    :param extend_all: Whether the policies or ACLs can be merged.
    :return: The final merged dict
    :rtype: dict
    """
    if keyisset("ACLs", override):
        override_acls = ACLs.parse_obj(override["ACLs"]).dict()
        if keypresent("Policies", override_acls) and not extend_all:
            del override_acls["Policies"]
            final["ACLs"].update(override_acls)
        elif keyisset("Policies", override_acls) and extend_all:
            if keyisset("Policies", final["ACLs"]):
                merged_lists = override_acls["Policies"] + final["ACLs"]["Policies"]
            else:
                merged_lists = override_acls["Policies"]
            acls = [dict(y) for y in set(tuple(x.items()) for x in merged_lists)]
            final["ACLs"].update(override_acls)
            final["ACLs"]["Policies"] = acls


def merge_contents(primary, override, extend_all=False):
    """
    Function to override and update settings from override to primary
    :param primary:
    :param override:
    :param extend_all: Whether the policies or ACLs can be merged.
    :return: The final merged dict
    :rtype: dict
    """
    if not isinstance(override, dict):
        raise TypeError(
            "The content of the override file does not match the expected content pattern."
        )
    final = dict(deepcopy(primary))
    if (
        keypresent("Globals", final)
        and keyisset("Globals", override)
        and isinstance(override["Globals"], dict)
    ):
        override_globals = EwsKafkaParmeters.parse_obj(override["Globals"])
        final["Globals"].update(override_globals.dict())

    if (
        keypresent("Schemas", final)
        and keyisset("Schemas", override)
        and isinstance(override["Schemas"], dict)
    ):
        override_globals = SchemasDef.parse_obj(override["Schemas"])
        final["Schemas"].update(override_globals.dict())
    elif (
        not keypresent("Schemas", final)
        and keyisset("Schemas", override)
        and isinstance(override["Schemas"], dict)
    ):
        override_globals = SchemasDef.parse_obj(override["Schemas"])
        final["Schemas"] = override_globals.dict()

    merge_acls(final, override, extend_all)
    merge_topics(final, override, extend_all)
    return final


class KafkaStack(object):
    """
    Class to represent the Kafka topics / acls / schemas in CloudFormation.
    """

    def __init__(self, files_paths, config_file_path=None):
        self.model = None
        self.template = Template("Kafka topics-acls-schemas root")
        self.stack = None
        self.topic_class = RTopic
        self.acl_class = RACLs
        self.schemas_r = {}
        self.topics_r = {}
        self.globals_config = {}
        final_content = {"Globals": {}, "Topics": {}, "ACLs": {}}
        for file_path in files_paths:
            if file_path.endswith(".yaml") or file_path.endswith(".yml"):
                with open(file_path, "r") as file_fd:
                    file_content = file_fd.read()
                yaml_content = yaml.load(file_content, Loader=CfnYamlLoader)
                final_content = merge_contents(
                    final_content, yaml_content, extend_all=True
                )
        if config_file_path:
            with open(config_file_path, "r") as override_fd:
                override_content = override_fd.read()
            override_content = yaml.load(override_content, Loader=CfnYamlLoader)
            final_content = merge_contents(final_content, override_content)
        self.model = Model.parse_obj(final_content)

        if not self.model.Topics and not self.model.ACLs:
            raise KeyError("You must define at least one of ACLs or Topics")
        self.set_globals()

    def set_globals(self):
        """
        Method to set the global settings
        """
        self.globals_config.update(
            {
                "BootstrapServers": self.model.Globals.BootstrapServers,
                "SASLUsername": self.model.Globals.SASLUsername
                if self.model.Globals.SASLUsername
                else Ref(AWS_NO_VALUE),
                "SASLPassword": self.model.Globals.SASLPassword
                if self.model.Globals.SASLPassword
                else Ref(AWS_NO_VALUE),
                "SASLMechanism": SASLMechanism[
                    self.model.Globals.SASLMechanism.name
                ].value
                if isinstance(self.model.Globals.SASLMechanism, SASLMechanism)
                else self.model.Globals.SASLMechanism,
                "SecurityProtocol": SecurityProtocol[
                    self.model.Globals.SecurityProtocol.name
                ].value
                if isinstance(self.model.Globals.SecurityProtocol, SecurityProtocol)
                else self.model.Globals.SecurityProtocol,
            }
        )

    def add_topic_schema(self, topic_name, schema_definition):
        """
        Method to create a new schema for the given topic

        :param str topic_name:
        :param Schema schema_definition:
        """
        schema_class = RTopicSchema
        if self.model.Schemas and self.model.Schemas.FunctionName:
            schema_class = CTopicSchema
        schema_config = schema_definition.dict()
        if self.model.Schemas and self.model.Schemas.RegistryUrl:
            registry_url = self.model.Schemas.RegistryUrl
        elif keyisset("RegistryUrl", schema_config):
            registry_url = schema_config["RegistryUrl"]
        else:
            raise KeyError(
                "RegistryUrl is not defined in Schema settings nor in Schemas"
            )
        if self.model.Schemas and self.model.Schemas.RegistryUsername:
            registry_username = self.model.Schemas.RegistryUsername
        elif keyisset("RegistryUsername", schema_config):
            registry_username = schema_config["RegistryUsername"]
        else:
            registry_username = Ref(AWS_NO_VALUE)
        if self.model.Schemas and self.model.Schemas.RegistryPassword:
            registry_password = self.model.Schemas.RegistryPassword
        elif keyisset("RegistryPassword", schema_config):
            registry_password = schema_config["RegistryPassword"]
        else:
            registry_password = Ref(AWS_NO_VALUE)

        if isinstance(schema_definition.Definition, str):
            try:
                with open(schema_definition.Definition, "r") as definition_fd:
                    definition = json.dumps(json.loads(definition_fd.read()))
            except FileNotFoundError:
                print("Failed to load file, using string literal as definition")
                definition = schema_definition.Definition
        else:
            definition = schema_definition.Definition

        topic_schema_r = schema_class(
            f"{topic_name}{SchemaType[schema_definition.Type.name].value}"
            f"{SerializeAttribute[schema_definition.SerializeAttribute.name].value}Schema",
            SerializeAttribute=SerializeAttribute[
                schema_definition.SerializeAttribute.name
            ].value,
            Type=SchemaType[schema_definition.Type.name].value,
            Definition=definition,
            Subject=Ref(topic_name),
            RegistryUrl=registry_url,
            RegistryUsername=registry_username,
            RegistryPassword=registry_password,
            CompatibilityMode=CompatibilityMode[
                schema_definition.CompatibilityMode.name
            ].value,
        )
        self.schemas_r[topic_name] = topic_schema_r
        self.template.add_resource(topic_schema_r)

    def render_topics(self):
        if not self.model.Topics or not self.model.Topics.Topics:
            print("No Topics defined")
            return
        function_name = None
        if self.model.Topics.FunctionName:
            self.topic_class = CTopic
            function_name = (
                function_name
                if self.model.Topics.FunctionName.startswith("arn:aws")
                else Sub(
                    "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:"
                    f"{self.model.Topics.FunctionName}"
                )
            )
        for topic in self.model.Topics.Topics:
            topic_cfg = topic.dict()
            if function_name:
                topic_cfg.update({"ServiceToken": function_name})
            topic_cfg.update(self.globals_config)
            topic_cfg.update(
                {
                    "ReplicationFactor": self.model.Topics.ReplicationFactor.__root__
                    if not topic.ReplicationFactor
                    else topic.ReplicationFactor,
                }
            )
            if not keyisset("Settings", topic_cfg) and keypresent(
                "Settings", topic_cfg
            ):
                del topic_cfg["Settings"]
            topic_title_raw = topic.Name.__root__
            topic_title = topic_title_raw.replace("-", "").title()
            topic_title = NONALPHANUM.sub("", topic_title)
            if topic.Schema and keyisset("Schema", topic_cfg):
                # self.add_topic_schema(topic_title, topic.Schema)
                del topic_cfg["Schema"]
            if keypresent("Schema", topic_cfg):
                del topic_cfg["Schema"]
            topic_r = self.template.add_resource(
                self.topic_class(
                    topic_title,
                    DeletionPolicy=DeletionPolicy[
                        self.model.Topics.DeletionPolicy.name
                    ].value,
                    **topic_cfg,
                )
            )
            self.topics_r[topic.Name.__root__] = topic_r

    def import_topic_name(self, policy):
        """
        Method to identify whether the resource provided is a topic referenced in the template

        :param Policy policy:
        :return:
        """
        if policy.ResourceType.value == ResourceType.TOPIC.name:
            topic_name = policy.Resource
            if topic_name in self.topics_r.keys():
                return GetAtt(self.topics_r[policy.Resource], "Name")
        return policy.Resource

    def render_acls(self):
        if not self.model.ACLs or not self.model.ACLs.Policies:
            return
        function_name = None
        if self.model.ACLs.FunctionName:
            self.acl_class = CACLs
            function_name = (
                function_name
                if self.model.ACLs.FunctionName.startswith("arn:aws")
                else Sub(
                    "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:"
                    f"{self.model.ACLs.FunctionName}"
                )
            )
        acl = {}
        acl.update(self.globals_config)
        if function_name:
            acl.update({"ServiceToken": function_name})

        policies = []
        for policy in self.model.ACLs.Policies:
            if isinstance(policy.PatternType, str):
                pattern_key = policy.PatternType
            else:
                pattern_key = policy.PatternType.name
            policies.append(
                KafkaAclPolicy(
                    Resource=self.import_topic_name(policy),
                    ResourceType=ResourceType[policy.ResourceType.name].value,
                    Principal=policy.Principal,
                    PatternType=PatternType[pattern_key].value,
                    Action=Action[policy.Action.name].value,
                    Effect=Effect[policy.Effect.name].value,
                    Host=policy.Host if policy.Host else r"*",
                )
            )
        self.template.add_resource(self.acl_class("ACLs", Policies=policies, **acl))
