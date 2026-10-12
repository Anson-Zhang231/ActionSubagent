from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from typing import Any

from action_runtime.domain.capability import (
    BusinessRuleDefinition,
    ComparisonOperator,
)

Comparator = Callable[[Any, Any], bool]

_COMPARATORS: dict[ComparisonOperator, Comparator] = {
    ComparisonOperator.LESS_THAN: operator.lt,
    ComparisonOperator.LESS_THAN_OR_EQUAL: operator.le,
    ComparisonOperator.GREATER_THAN: operator.gt,
    ComparisonOperator.GREATER_THAN_OR_EQUAL: operator.ge,
    ComparisonOperator.EQUAL: operator.eq,
    ComparisonOperator.NOT_EQUAL: operator.ne,
}


def validate_business_rules(
    rules: tuple[BusinessRuleDefinition, ...],
    parameters: Mapping[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    for rule in rules:
        if rule.left_field not in parameters:
            continue

        if rule.right_field not in parameters:
            continue

        left_value = parameters[rule.left_field]
        right_value = parameters[rule.right_field]

        if left_value is None or right_value is None:
            continue

        comparator = _COMPARATORS[rule.operator]

        try:
            rule_passed = comparator(left_value, right_value)
        except (TypeError, ValueError):
            rule_passed = False

        if rule_passed:
            continue

        issues.append(
            {
                "field": rule.target_field,
                "code": "business_rule_failed",
                "message": rule.message,
            }
        )

    return issues
