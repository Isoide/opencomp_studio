from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from opencomp.core.models import ExpressionBinding, Node, ProjectGraph, ProjectSettings


class ExpressionError(RuntimeError):
    def __init__(self, node_id: str, param_key: str, source: str, message: str) -> None:
        super().__init__(f"{node_id}.{param_key}: {message}")
        self.node_id = node_id
        self.param_key = param_key
        self.source = source
        self.message = message


@dataclass(slots=True)
class ResolvedNodeState:
    params: dict[str, Any]
    expression_errors: dict[str, str]
    bindable_outputs: dict[str, Any]


class ExpressionResolver:
    def __init__(self, graph: ProjectGraph, settings: ProjectSettings, frame: int) -> None:
        self.graph = graph
        self.settings = settings
        self.frame = int(frame)
        self._resolved: dict[str, ResolvedNodeState] = {}
        self._resolving_nodes: set[str] = set()
        self._resolving_params: list[tuple[str, str]] = []

    def resolve_node(self, node_id: str) -> ResolvedNodeState:
        cached = self._resolved.get(node_id)
        if cached is not None:
            return cached
        if node_id in self._resolving_nodes:
            chain = " -> ".join([*self._resolving_nodes, node_id])
            raise ExpressionError(node_id, "*", "", f"Expression node cycle detected: {chain}")
        node = self.graph.nodes[node_id]
        self._resolving_nodes.add(node_id)
        try:
            params = dict(node.params)
            errors: dict[str, str] = {}
            for key, binding in node.param_expressions.items():
                if not binding.enabled or not binding.source.strip():
                    continue
                try:
                    params[key] = self._evaluate_binding(node, key, binding)
                except ExpressionError as exc:
                    errors[key] = exc.message
            bindable_outputs = {
                **params,
                "_errors": errors,
                "_meta": {
                    "node_id": node.id,
                    "type": node.type,
                    "frame": self.frame,
                },
            }
            resolved = ResolvedNodeState(params=params, expression_errors=errors, bindable_outputs=bindable_outputs)
            self._resolved[node_id] = resolved
            return resolved
        finally:
            self._resolving_nodes.discard(node_id)

    def node_signature_payload(self, node: Node) -> dict[str, Any]:
        resolved = self.resolve_node(node.id)
        expressions = {
            key: {
                "source": binding.source,
                "enabled": binding.enabled,
                "compiled_cache_key": binding.compiled_cache_key or expression_cache_key(binding),
            }
            for key, binding in node.param_expressions.items()
        }
        return {
            "resolved_params": resolved.params,
            "expression_errors": resolved.expression_errors,
            "param_expressions": expressions,
        }

    def bindable_outputs(self, node_id: str) -> dict[str, Any]:
        return self.resolve_node(node_id).bindable_outputs

    def resolved_params(self, node_id: str) -> dict[str, Any]:
        return dict(self.resolve_node(node_id).params)

    def expression_errors(self, node_id: str) -> dict[str, str]:
        return dict(self.resolve_node(node_id).expression_errors)

    def _evaluate_binding(self, node: Node, param_key: str, binding: ExpressionBinding) -> float:
        marker = (node.id, param_key)
        if marker in self._resolving_params:
            chain = " -> ".join(f"{node_id}.{key}" for node_id, key in [*self._resolving_params, marker])
            raise ExpressionError(node.id, param_key, binding.source, f"Expression parameter cycle detected: {chain}")
        self._resolving_params.append(marker)
        try:
            tree = ast.parse(binding.source, mode="eval")
            return float(self._eval_expr(node.id, param_key, binding.source, tree.body))
        except ExpressionError:
            raise
        except Exception as exc:
            raise ExpressionError(node.id, param_key, binding.source, str(exc)) from exc
        finally:
            self._resolving_params.pop()

    def _eval_expr(self, owner_node_id: str, param_key: str, source: str, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node.value, str):
                return node.value
            raise ExpressionError(owner_node_id, param_key, source, f"Unsupported constant '{node.value}'.")
        if isinstance(node, ast.Name):
            if node.id == "frame":
                return float(self.frame)
            if node.id == "fps":
                return float(self.settings.fps)
            raise ExpressionError(owner_node_id, param_key, source, f"Unknown identifier '{node.id}'.")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -float(self._eval_expr(owner_node_id, param_key, source, node.operand))
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = float(self._eval_expr(owner_node_id, param_key, source, node.left))
            right = float(self._eval_expr(owner_node_id, param_key, source, node.right))
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return self._call_builtin(owner_node_id, param_key, source, node.func.id, node.args)
            if isinstance(node.func, ast.Attribute):
                target = self._eval_expr(owner_node_id, param_key, source, node.func.value)
                attr_name = node.func.attr
                func = self._lookup_attr(owner_node_id, param_key, source, target, attr_name)
                if not callable(func):
                    raise ExpressionError(owner_node_id, param_key, source, f"'{attr_name}' is not callable.")
                values = [self._eval_expr(owner_node_id, param_key, source, arg) for arg in node.args]
                return func(*values)
            raise ExpressionError(owner_node_id, param_key, source, "Unsupported function call.")
        if isinstance(node, ast.Attribute):
            target = self._eval_expr(owner_node_id, param_key, source, node.value)
            return self._lookup_attr(owner_node_id, param_key, source, target, node.attr)
        raise ExpressionError(owner_node_id, param_key, source, f"Unsupported expression element '{type(node).__name__}'.")

    def _call_builtin(
        self,
        owner_node_id: str,
        param_key: str,
        source: str,
        func_name: str,
        args: list[ast.AST],
    ) -> Any:
        if func_name == "node":
            if len(args) != 1:
                raise ExpressionError(owner_node_id, param_key, source, "node() expects exactly one node id string.")
            node_id_value = self._eval_expr(owner_node_id, param_key, source, args[0])
            if not isinstance(node_id_value, str):
                raise ExpressionError(owner_node_id, param_key, source, "node() expects a string node id.")
            if node_id_value not in self.graph.nodes:
                raise ExpressionError(owner_node_id, param_key, source, f"Unknown node '{node_id_value}'.")
            return self.bindable_outputs(node_id_value)

        values = [float(self._eval_expr(owner_node_id, param_key, source, arg)) for arg in args]
        if func_name == "min":
            return min(values)
        if func_name == "max":
            return max(values)
        if func_name == "clamp":
            if len(values) != 3:
                raise ExpressionError(owner_node_id, param_key, source, "clamp() expects 3 arguments.")
            return max(values[1], min(values[0], values[2]))
        if func_name == "abs":
            if len(values) != 1:
                raise ExpressionError(owner_node_id, param_key, source, "abs() expects 1 argument.")
            return abs(values[0])
        if func_name == "floor":
            return math.floor(values[0])
        if func_name == "ceil":
            return math.ceil(values[0])
        if func_name == "round":
            return round(values[0])
        if func_name == "lerp":
            if len(values) != 3:
                raise ExpressionError(owner_node_id, param_key, source, "lerp() expects 3 arguments.")
            return values[0] + (values[1] - values[0]) * values[2]
        raise ExpressionError(owner_node_id, param_key, source, f"Unsupported function '{func_name}'.")

    def _lookup_attr(self, owner_node_id: str, param_key: str, source: str, target: Any, attr: str) -> Any:
        if isinstance(target, dict):
            if attr in target.get("_errors", {}):
                raise ExpressionError(owner_node_id, param_key, source, str(target["_errors"][attr]))
            if attr in target:
                return target[attr]
            if attr in {"x", "y"} and attr in target:
                return target[attr]
        raise ExpressionError(owner_node_id, param_key, source, f"Unknown attribute '{attr}'.")


def expression_cache_key(binding: ExpressionBinding) -> str:
    payload = {
        "source": binding.source,
        "enabled": binding.enabled,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
