"""源码契约错误：只携带索引标识和数字行范围，不携带源码或模型原文。"""
from __future__ import annotations


class SourceContractError(ValueError):
    def __init__(self, message, identifier, implementation_lines=None, supplied_lines=None, allowed_identifiers=None,
                 target_identifier=None):
        super().__init__(message)
        self.identifier = identifier
        self.implementation_lines = implementation_lines
        self.supplied_lines = supplied_lines or []
        self.allowed_identifiers = allowed_identifiers or []
        self.target_identifier = target_identifier
