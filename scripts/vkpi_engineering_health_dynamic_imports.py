"""Fail-closed finite-target analysis for Python dynamic imports.

The analyzer never imports candidate code.  It evaluates only a deliberately
small AST language (literals, finite containers, mappings, comprehensions,
string formatting, and finite lookups) and enumerates direct call sites for
function parameters.  Anything outside that language remains unresolved.
"""
from __future__ import annotations

import ast
import itertools
from typing import Any, Iterable, Mapping

try:
    from scripts import vkpi_engineering_health_dynamic_types as dynamic_types
except ModuleNotFoundError:  # direct script execution adds scripts/, not repository root
    import vkpi_engineering_health_dynamic_types as dynamic_types

MAX_DOMAIN_VALUES = dynamic_types.MAX_DOMAIN_VALUES
Domain = dynamic_types.Domain
FrozenMap = dynamic_types.FrozenMap
DynamicImportFinding = dynamic_types.DynamicImportFinding
_CallSite = dynamic_types.CallSite


def _unique(values: Iterable[object]) -> Domain | None:
    ordered: list[object] = []
    seen: set[object] = set()
    for value in values:
        try:
            key = (type(value).__name__, value)
            if key in seen:
                continue
            seen.add(key)
        except TypeError:
            return None
        ordered.append(value)
        if len(ordered) > MAX_DOMAIN_VALUES:
            return None
    return tuple(ordered)


def _combine(domains: list[Domain]) -> Iterable[tuple[object, ...]]:
    if not domains:
        return [()]
    size = 1
    for domain in domains:
        size *= len(domain)
        if size > MAX_DOMAIN_VALUES:
            return []
    return itertools.product(*domains)


def _sequence_items(domain: Domain | None) -> Domain | None:
    if domain is None:
        return None
    values: list[object] = []
    for value in domain:
        if not isinstance(value, tuple):
            return None
        values.extend(value)
    return _unique(values)


def _lookup_mapping(mapping: FrozenMap, keys: Domain | None, default: Domain | None) -> Domain | None:
    if keys is None:
        values = [value for _, value in mapping.items]
        values.extend(default or ())
        return _unique(values)
    table = dict(mapping.items)
    values: list[object] = []
    for key in keys:
        if key in table:
            values.append(table[key])
        elif default is not None:
            values.extend(default)
    return _unique(values)


def _eval_sequence(
    elements: list[ast.expr],
    env: Mapping[str, Domain],
    module: str,
) -> Domain | None:
    domains = [_eval_expr(element, env, module) for element in elements]
    if any(domain is None for domain in domains):
        return None
    combinations = _combine([domain for domain in domains if domain is not None])
    return _unique(tuple(values) for values in combinations)


def _eval_dict(node: ast.Dict, env: Mapping[str, Domain], module: str) -> Domain | None:
    if any(key is None for key in node.keys):
        return None
    keys = [_eval_expr(key, env, module) for key in node.keys if key is not None]
    values = [_eval_expr(value, env, module) for value in node.values]
    if any(domain is None for domain in [*keys, *values]):
        return None
    pair_domains = [
        tuple((key, value) for key in key_domain for value in value_domain)
        for key_domain, value_domain in zip(keys, values)
        if key_domain is not None and value_domain is not None
    ]
    combinations = _combine(pair_domains)
    return _unique(FrozenMap(tuple(pairs)) for pairs in combinations)


def _bind_target(target: ast.expr, domain: Domain | None, env: dict[str, Domain]) -> None:
    if isinstance(target, ast.Name):
        if domain is None:
            env.pop(target.id, None)
        else:
            env[target.id] = domain
        return
    if not isinstance(target, (ast.Tuple, ast.List)) or domain is None:
        return
    per_position: list[list[object]] = [[] for _ in target.elts]
    for value in domain:
        if not isinstance(value, tuple) or len(value) != len(target.elts):
            continue
        for index, item in enumerate(value):
            per_position[index].append(item)
    for child, values in zip(target.elts, per_position):
        _bind_target(child, _unique(values), env)


def _eval_dict_comp(node: ast.DictComp, env: Mapping[str, Domain], module: str) -> Domain | None:
    if len(node.generators) != 1 or node.generators[0].is_async:
        return None
    generator = node.generators[0]
    items = _sequence_items(_eval_expr(generator.iter, env, module))
    if items is None:
        return None
    pairs: list[tuple[object, object]] = []
    for item in items:
        local = dict(env)
        _bind_target(generator.target, (item,), local)
        if generator.ifs:
            tests = [_eval_expr(condition, local, module) for condition in generator.ifs]
            if any(test is None or set(test) != {True} for test in tests):
                return None
        keys = _eval_expr(node.key, local, module)
        values = _eval_expr(node.value, local, module)
        if keys is None or values is None:
            return None
        pairs.extend((key, value) for key in keys for value in values)
    return (FrozenMap(tuple(pairs)),)


def _eval_joined_str(node: ast.JoinedStr, env: Mapping[str, Domain], module: str) -> Domain | None:
    parts: list[Domain] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append((value.value,))
            continue
        if not isinstance(value, ast.FormattedValue) or value.format_spec is not None:
            return None
        domain = _eval_expr(value.value, env, module)
        if domain is None or not all(isinstance(item, (str, int)) for item in domain):
            return None
        parts.append(tuple(str(item) for item in domain))
    combinations = _combine(parts)
    return _unique("".join(values) for values in combinations)


def _eval_expr(node: ast.AST | None, env: Mapping[str, Domain], module: str) -> Domain | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, bool, type(None))):
        return (node.value,)
    if isinstance(node, ast.Name):
        if node.id == "__name__":
            return (module,)
        return env.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return _eval_sequence(list(node.elts), env, module)
    if isinstance(node, ast.Dict):
        return _eval_dict(node, env, module)
    if isinstance(node, ast.DictComp):
        return _eval_dict_comp(node, env, module)
    if isinstance(node, ast.JoinedStr):
        return _eval_joined_str(node, env, module)
    if isinstance(node, ast.NamedExpr):
        return _eval_expr(node.value, env, module)
    if isinstance(node, ast.IfExp):
        left = _eval_expr(node.body, env, module)
        right = _eval_expr(node.orelse, env, module)
        return None if left is None or right is None else _unique((*left, *right))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _eval_expr(node.left, env, module)
        right = _eval_expr(node.right, env, module)
        if left is None or right is None:
            return None
        values: list[object] = []
        for first in left:
            for second in right:
                if isinstance(first, str) and isinstance(second, str):
                    values.append(first + second)
                elif isinstance(first, tuple) and isinstance(second, tuple):
                    values.append(first + second)
                else:
                    return None
        return _unique(values)
    if isinstance(node, ast.Subscript):
        container = _eval_expr(node.value, env, module)
        keys = _eval_expr(node.slice, env, module)
        if container is None:
            return None
        if all(isinstance(value, FrozenMap) for value in container):
            values: list[object] = []
            for mapping in container:
                found = _lookup_mapping(mapping, keys, None)
                if found is not None:
                    values.extend(found)
            return _unique(values)
        if keys is None and all(isinstance(value, tuple) for value in container):
            return _sequence_items(container)
        return None
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) in {1, 2}
            and not node.keywords
        ):
            mappings = _eval_expr(node.func.value, env, module)
            keys = _eval_expr(node.args[0], env, module)
            default = _eval_expr(node.args[1], env, module) if len(node.args) == 2 else (None,)
            if mappings is None or not all(isinstance(value, FrozenMap) for value in mappings):
                return None
            values: list[object] = []
            for mapping in mappings:
                found = _lookup_mapping(mapping, keys, default)
                if found is not None:
                    values.extend(found)
            return _unique(values)
        if isinstance(node.func, ast.Name) and node.func.id in {"tuple", "list", "set", "frozenset"}:
            if len(node.args) != 1 or node.keywords:
                return None
            items = _sequence_items(_eval_expr(node.args[0], env, module))
            return None if items is None else (items,)
    return None


def _absolute_import_module(module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = module.split(".")[:-1]
    ascend = node.level - 1
    if ascend > len(package):
        return None
    anchor = package[: len(package) - ascend]
    if node.module:
        anchor.extend(node.module.split("."))
    return ".".join(anchor)


def _imported_symbols(module: str, tree: ast.Module) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                symbols[alias.asname or alias.name.split(".")[0]] = alias.name
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_module = _absolute_import_module(module, node)
        if not imported_module:
            continue
        for alias in node.names:
            if alias.name != "*":
                symbols[alias.asname or alias.name] = f"{imported_module}.{alias.name}"
    return symbols


def _module_environments(
    trees_by_module: Mapping[str, ast.Module],
) -> tuple[dict[str, dict[str, Domain]], dict[str, dict[str, str]]]:
    environments = {module: {} for module in trees_by_module}
    imports = {module: _imported_symbols(module, tree) for module, tree in trees_by_module.items()}
    for _ in range(min(len(trees_by_module) + 1, 32)):
        changed = False
        for module in sorted(trees_by_module):
            env = dict(environments[module])
            for local, symbol in imports[module].items():
                source_module, _, source_name = symbol.rpartition(".")
                domain = environments.get(source_module, {}).get(source_name)
                if domain is not None:
                    env[local] = domain
            for statement in trees_by_module[module].body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    domain = _eval_expr(value, env, module)
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                    for target in targets:
                        _bind_target(target, domain, env)
            if env != environments[module]:
                environments[module] = env
                changed = True
        if not changed:
            break
    return environments, imports


def _dynamic_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    modules = {"importlib"}
    functions = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    functions.add(alias.asname or alias.name)
    return modules, functions


def _dynamic_callee(call: ast.Call, modules: set[str], functions: set[str]) -> str | None:
    function = call.func
    if isinstance(function, ast.Name) and function.id in functions:
        return function.id
    if (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id in modules
    ):
        return f"{function.value.id}.import_module"
    return None


def _merge_envs(environments: Iterable[Mapping[str, Domain]]) -> dict[str, Domain]:
    rows = list(environments)
    if not rows:
        return {}
    merged: dict[str, Domain] = {}
    for name in set.intersection(*(set(row) for row in rows)):
        domain = _unique(value for row in rows for value in row[name])
        if domain is not None:
            merged[name] = domain
    return merged


def _block_terminates(statements: list[ast.stmt]) -> bool:
    if not statements:
        return False
    tail = statements[-1]
    if isinstance(tail, (ast.Return, ast.Raise)):
        return True
    return isinstance(tail, ast.If) and _block_terminates(tail.body) and _block_terminates(tail.orelse)


def _refine_condition(test: ast.expr, truth: bool, env: Mapping[str, Domain], module: str) -> dict[str, Domain]:
    refined = dict(env)
    if isinstance(test, ast.NamedExpr) and isinstance(test.target, ast.Name):
        name = test.target.id
        if name in refined:
            domain = _unique(value for value in refined[name] if bool(value) is truth)
            if domain is not None:
                refined[name] = domain
        return refined
    if isinstance(test, ast.Name) and test.id in refined:
        domain = _unique(value for value in refined[test.id] if bool(value) is truth)
        if domain is not None:
            refined[test.id] = domain
        return refined
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return refined
    left = test.left
    operator = test.ops[0]
    right = test.comparators[0]
    if isinstance(left, ast.Name) and isinstance(operator, (ast.Is, ast.IsNot)):
        if isinstance(right, ast.Constant) and right.value is None and left.id in refined:
            keep_none = truth is isinstance(operator, ast.Is)
            domain = _unique(value for value in refined[left.id] if (value is None) is keep_none)
            if domain:
                refined[left.id] = domain
        return refined
    membership = isinstance(operator, ast.In) and truth or isinstance(operator, ast.NotIn) and not truth
    if isinstance(left, ast.Name) and membership:
        items = _sequence_items(_eval_expr(right, env, module))
        if items is not None:
            refined[left.id] = items
    return refined


class _Scanner:
    def __init__(
        self,
        *,
        module: str,
        path: str,
        module_env: Mapping[str, Domain],
        imports: Mapping[str, str],
        param_domains: Mapping[str, Domain],
        known_modules: set[str],
        collect_dynamic: bool,
    ) -> None:
        self.module = module
        self.path = path
        self.module_env = dict(module_env)
        self.imports = dict(imports)
        self.param_domains = dict(param_domains)
        self.known_modules = known_modules
        self.collect_dynamic = collect_dynamic
        self.callsites: list[_CallSite] = []
        self.findings: dict[tuple[int, int], DynamicImportFinding] = {}
        self.dynamic_modules: set[str] = set()
        self.dynamic_functions: set[str] = set()
        self._callsite_bound: set[str] = set()

    def scan(self, tree: ast.Module) -> None:
        self.dynamic_modules, self.dynamic_functions = _dynamic_aliases(tree)
        self._scan_block(tree.body, dict(self.module_env), top_level=True)

    def _call_symbol(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.imports:
                return self.imports[node.id]
            return f"{self.module}.{node.id}"
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and self.imports.get(node.value.id) in self.known_modules
        ):
            return f"{self.imports[node.value.id]}.{node.attr}"
        return None

    def _record_call(self, call: ast.Call, env: Mapping[str, Domain]) -> None:
        symbol = self._call_symbol(call.func)
        if symbol is not None:
            self.callsites.append(
                _CallSite(
                    symbol=symbol,
                    positional=tuple(_eval_expr(argument, env, self.module) for argument in call.args),
                    keywords=tuple(
                        (keyword.arg, _eval_expr(keyword.value, env, self.module))
                        for keyword in call.keywords
                        if keyword.arg is not None
                    ),
                )
            )
        if not self.collect_dynamic:
            return
        callee = _dynamic_callee(call, self.dynamic_modules, self.dynamic_functions)
        if callee is None:
            return
        argument = call.args[0] if call.args else None
        domain = _eval_expr(argument, env, self.module)
        key = (int(call.lineno), int(call.col_offset))
        literal = isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        if domain is None or not domain or not all(isinstance(value, str) for value in domain):
            self.findings[key] = DynamicImportFinding(
                self.path, key[0], key[1], callee, (), (), None,
                "non_literal_or_relative_argument", literal,
            )
            return
        names = tuple(sorted(set(domain)))
        if any(name.startswith(".") for name in names):
            self.findings[key] = DynamicImportFinding(
                self.path, key[0], key[1], callee, (), (), None,
                "non_literal_or_relative_argument", literal,
            )
            return
        internal = tuple(name for name in names if name == "app" or name.startswith("app."))
        missing = tuple(name for name in internal if name not in self.known_modules)
        present = tuple(name for name in internal if name in self.known_modules)
        reason = None
        if missing:
            reason = "constant_internal_module_not_found" if literal else "finite_internal_module_not_found"
        kind = "literal" if literal else (
            "finite_callsite_enumeration"
            if any(name in self._callsite_bound for name in _names_in(argument))
            else "finite_ast_constant_propagation"
        )
        self.findings[key] = DynamicImportFinding(
            self.path, key[0], key[1], callee, present, missing, kind, reason, literal,
        )

    def _scan_expr(self, node: ast.AST | None, env: dict[str, Domain]) -> None:
        if node is None:
            return
        if isinstance(node, ast.NamedExpr):
            self._scan_expr(node.value, env)
            _bind_target(node.target, _eval_expr(node.value, env, self.module), env)
            return
        if isinstance(node, ast.Lambda):
            local = dict(env)
            for argument in _function_arguments(node.args):
                local.pop(argument, None)
            self._scan_expr(node.body, local)
            return
        for child in ast.iter_child_nodes(node):
            self._scan_expr(child, env)
        if isinstance(node, ast.Call):
            self._record_call(node, env)

    def _function_env(self, node: ast.FunctionDef | ast.AsyncFunctionDef, closure: Mapping[str, Domain]) -> dict[str, Domain]:
        local = dict(closure)
        symbol = f"{self.module}.{node.name}"
        bound: set[str] = set()
        for name in _function_arguments(node.args):
            local.pop(name, None)
            key = f"{symbol}:{name}"
            if key in self.param_domains:
                local[name] = self.param_domains[key]
                bound.add(name)
        self._callsite_bound.update(bound)
        return local

    def _scan_block(self, statements: list[ast.stmt], env: dict[str, Domain], *, top_level: bool = False) -> dict[str, Domain]:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for expression in [*statement.decorator_list, *statement.args.defaults, *statement.args.kw_defaults]:
                    self._scan_expr(expression, env)
                local = self._function_env(statement, env)
                self._scan_block(statement.body, local)
                continue
            if isinstance(statement, ast.ClassDef):
                for expression in [*statement.decorator_list, *statement.bases, *[item.value for item in statement.keywords]]:
                    self._scan_expr(expression, env)
                self._scan_block(statement.body, dict(env))
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                self._scan_expr(value, env)
                domain = _eval_expr(value, env, self.module)
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    _bind_target(target, domain, env)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                self._scan_expr(statement.iter, env)
                body_env = dict(env)
                _bind_target(statement.target, _sequence_items(_eval_expr(statement.iter, env, self.module)), body_env)
                body_after = self._scan_block(statement.body, body_env)
                else_after = self._scan_block(statement.orelse, dict(env))
                env = _merge_envs([env, body_after, else_after])
                continue
            if isinstance(statement, ast.If):
                self._scan_expr(statement.test, env)
                yes = self._scan_block(
                    statement.body,
                    _refine_condition(statement.test, True, env, self.module),
                )
                no = self._scan_block(
                    statement.orelse,
                    _refine_condition(statement.test, False, env, self.module),
                )
                if _block_terminates(statement.body) and not _block_terminates(statement.orelse):
                    env = no
                elif _block_terminates(statement.orelse) and not _block_terminates(statement.body):
                    env = yes
                else:
                    env = _merge_envs([yes, no])
                continue
            if isinstance(statement, ast.Try):
                branches = [self._scan_block(statement.body, dict(env))]
                branches.extend(self._scan_block(handler.body, dict(env)) for handler in statement.handlers)
                if statement.orelse:
                    branches.append(self._scan_block(statement.orelse, dict(env)))
                env = _merge_envs(branches)
                env = self._scan_block(statement.finalbody, env)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                local = dict(env)
                for item in statement.items:
                    self._scan_expr(item.context_expr, local)
                    if item.optional_vars is not None:
                        _bind_target(item.optional_vars, None, local)
                env = self._scan_block(statement.body, local)
                continue
            if isinstance(statement, ast.While):
                self._scan_expr(statement.test, env)
                body = self._scan_block(statement.body, dict(env))
                other = self._scan_block(statement.orelse, dict(env))
                env = _merge_envs([env, body, other])
                continue
            if isinstance(statement, ast.Match):
                self._scan_expr(statement.subject, env)
                branches = []
                for case in statement.cases:
                    self._scan_expr(case.guard, env)
                    branches.append(self._scan_block(case.body, dict(env)))
                env = _merge_envs([env, *branches])
                continue
            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.expr):
                    self._scan_expr(child, env)
        return env


def _function_arguments(arguments: ast.arguments) -> list[str]:
    rows = [argument.arg for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]]
    if arguments.vararg:
        rows.append(arguments.vararg.arg)
    if arguments.kwarg:
        rows.append(arguments.kwarg.arg)
    return rows


def _names_in(node: ast.AST | None) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)} if node is not None else set()


def _function_index(trees_by_module: Mapping[str, ast.Module]) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    index: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for module, tree in trees_by_module.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index[f"{module}.{node.name}"] = node
    return index


def _is_direct_call_reference(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    return isinstance(parent, ast.Call) and parent.func is node


def _shadowed_names(nodes: Iterable[ast.AST]) -> set[str]:
    rows = list(nodes)
    names = {
        node.id
        for node in rows
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    for node in rows:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            names.update(_function_arguments(node.args))
    return names


def _complete_direct_reference_symbols(
    trees_by_module: Mapping[str, ast.Module],
    imports: Mapping[str, Mapping[str, str]],
    functions: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    known_modules: set[str],
) -> set[str]:
    complete = set(functions)
    functions_by_module: dict[str, set[str]] = {}
    for symbol in functions:
        functions_by_module.setdefault(symbol.rpartition(".")[0], set()).add(symbol)
    for module, tree in trees_by_module.items():
        nodes = list(ast.walk(tree))
        parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
        shadowed = _shadowed_names(nodes)
        loads: dict[str, list[ast.Name]] = {}
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads.setdefault(node.id, []).append(node)
        local_imports = imports[module]
        for local, target in local_imports.items():
            if target in functions:
                if local in shadowed:
                    complete.discard(target)
                    continue
                references = loads.get(local, [])
                if any(not _is_direct_call_reference(node, parents) for node in references):
                    complete.discard(target)
                continue
            if target not in known_modules:
                continue
            contained = functions_by_module.get(target, set())
            for node in loads.get(local, []):
                parent = parents.get(node)
                if not isinstance(parent, ast.Attribute) or parent.value is not node:
                    complete.difference_update(contained)
                    continue
                symbol = f"{target}.{parent.attr}"
                if symbol in contained and not _is_direct_call_reference(parent, parents):
                    complete.discard(symbol)
        for statement in nodes:
            if not isinstance(statement, ast.ImportFrom) or not any(alias.name == "*" for alias in statement.names):
                continue
            imported_module = _absolute_import_module(module, statement)
            if imported_module:
                complete.difference_update(
                    functions_by_module.get(imported_module, set())
                )
        for symbol in functions_by_module.get(module, set()):
            name = symbol.rpartition(".")[2]
            if name in shadowed:
                complete.discard(symbol)
                continue
            references = loads.get(name, [])
            if any(not _is_direct_call_reference(node, parents) for node in references):
                complete.discard(symbol)
    return complete


def _parameter_domains(
    trees_by_module: Mapping[str, ast.Module],
    paths: Mapping[str, str],
    environments: Mapping[str, Mapping[str, Domain]],
    imports: Mapping[str, Mapping[str, str]],
    known_modules: set[str],
) -> dict[str, Domain]:
    callsites: list[_CallSite] = []
    for module in sorted(trees_by_module):
        scanner = _Scanner(
            module=module,
            path=paths[module],
            module_env=environments[module],
            imports=imports[module],
            param_domains={},
            known_modules=known_modules,
            collect_dynamic=False,
        )
        scanner.scan(trees_by_module[module])
        callsites.extend(scanner.callsites)
    functions = _function_index(trees_by_module)
    complete_symbols = _complete_direct_reference_symbols(
        trees_by_module,
        imports,
        functions,
        known_modules,
    )
    result: dict[str, Domain] = {}
    by_symbol: dict[str, list[_CallSite]] = {}
    for callsite in callsites:
        by_symbol.setdefault(callsite.symbol, []).append(callsite)
    for symbol, function in functions.items():
        if symbol not in complete_symbols:
            continue
        sites = by_symbol.get(symbol, [])
        if not sites:
            continue
        positional_names = [argument.arg for argument in [*function.args.posonlyargs, *function.args.args]]
        keyword_names = {argument.arg for argument in function.args.kwonlyargs}
        for index, name in enumerate(positional_names):
            domains: list[Domain] = []
            complete = True
            for site in sites:
                keyword_map = dict(site.keywords)
                domain = site.positional[index] if index < len(site.positional) else keyword_map.get(name)
                if domain is None:
                    complete = False
                    break
                domains.append(domain)
            if complete:
                merged = _unique(value for domain in domains for value in domain)
                if merged:
                    result[f"{symbol}:{name}"] = merged
        for name in keyword_names:
            domains = [dict(site.keywords).get(name) for site in sites]
            if domains and all(domain is not None for domain in domains):
                merged = _unique(value for domain in domains if domain is not None for value in domain)
                if merged:
                    result[f"{symbol}:{name}"] = merged
    return result


def analyze_dynamic_imports(
    trees_by_path: Mapping[str, ast.Module],
    module_paths: Mapping[str, str],
) -> list[DynamicImportFinding]:
    """Resolve all recognized dynamic import calls or emit an unresolved row."""

    trees_by_module = {module: trees_by_path[path] for module, path in module_paths.items()}
    environments, imports = _module_environments(trees_by_module)
    known = set(module_paths)
    param_domains = _parameter_domains(
        trees_by_module,
        module_paths,
        environments,
        imports,
        known,
    )
    findings: list[DynamicImportFinding] = []
    for module in sorted(trees_by_module):
        scanner = _Scanner(
            module=module,
            path=module_paths[module],
            module_env=environments[module],
            imports=imports[module],
            param_domains=param_domains,
            known_modules=known,
            collect_dynamic=True,
        )
        scanner.scan(trees_by_module[module])
        found = scanner.findings
        dynamic_modules, dynamic_functions = _dynamic_aliases(trees_by_module[module])
        for node in ast.walk(trees_by_module[module]):
            if not isinstance(node, ast.Call) or _dynamic_callee(node, dynamic_modules, dynamic_functions) is None:
                continue
            key = (int(node.lineno), int(node.col_offset))
            if key not in found:
                found[key] = DynamicImportFinding(
                    module_paths[module], key[0], key[1],
                    _dynamic_callee(node, dynamic_modules, dynamic_functions) or "dynamic_import",
                    (), (), None, "analysis_not_reached", False,
                )
        findings.extend(found.values())
    return sorted(findings, key=lambda row: (row.path, row.line, row.column, row.callee))
