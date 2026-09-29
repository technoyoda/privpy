"""Compile a deliberately restricted Python AST to data-only native-runtime IR."""
import ast
import builtins
import json

from ._wire import encode
from .errors import UnsupportedSyntaxError
from .intrinsics import _Intrinsic

BUILTINS = {"len", "bool", "str", "abs", "sum", "min", "max", "range"}
METHODS = {"get", "keys", "values", "items", "encode"}
BINOPS = {"Add", "Sub", "Mult", "Div", "FloorDiv", "Mod"}
COMPARE = {"Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In", "NotIn", "Is", "IsNot"}


class Compiler:
    def __init__(self, function):
        self.function = function
        self.dependencies = {}
        self.loop_depth = 0
        try:
            module = ast.parse(function._source)
        except (SyntaxError, RecursionError):
            raise UnsupportedSyntaxError("Function source cannot be parsed") from None
        if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
            raise UnsupportedSyntaxError("A private function must be a synchronous function definition")
        self.node = module.body[0]
        if function._function.__closure__:
            raise UnsupportedSyntaxError("Captured closures are unsupported")
        args = self.node.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.defaults:
            raise UnsupportedSyntaxError("Defaults, variadic and keyword-only parameters are unsupported")
        self.params = [arg.arg for arg in args.posonlyargs + args.args]
        self.locals = set(self.params)
        class LocalBindings(ast.NodeVisitor):
            def visit_Name(visitor, node):
                if isinstance(node.ctx, ast.Store):
                    self.locals.add(node.id)

            def visit_ListComp(visitor, node):
                # Comprehension targets have their own lexical scope.
                return

        for statement in self.node.body:
            LocalBindings().visit(statement)

    def reject(self, node):
        raise UnsupportedSyntaxError(
            "Unsupported {} at source line {}".format(type(node).__name__, getattr(node, "lineno", 0))
        )

    def literal(self, value):
        try:
            return {"k": "literal", "v": encode(value)}
        except (ValueError, TypeError, UnicodeError):
            raise UnsupportedSyntaxError("Unsupported literal") from None

    def resolve(self, name):
        if name == self.node.name:
            return self.function
        namespace = self.function._function.__globals__
        if name in namespace:
            return namespace[name]
        if name in BUILTINS:
            return getattr(builtins, name)
        raise UnsupportedSyntaxError("Unbound global name: " + name)

    def expression(self, node):
        if isinstance(node, ast.Constant):
            return self.literal(node.value)
        if isinstance(node, ast.Name):
            if node.id in self.locals:
                return {"k": "name", "id": node.id}
            value = self.resolve(node.id)
            if type(value) not in (type(None), bool, int, float, str, bytes):
                self.reject(node)
            return self.literal(value)
        if isinstance(node, ast.List):
            return {"k": "list", "items": [self.expression(x) for x in node.elts]}
        if isinstance(node, ast.Dict):
            if any(x is None for x in node.keys):
                self.reject(node)
            return {"k": "dict", "items": [
                [self.expression(k), self.expression(v)] for k, v in zip(node.keys, node.values)
            ]}
        if isinstance(node, ast.BinOp):
            op = type(node.op).__name__
            if op not in BINOPS:
                self.reject(node)
            return {"k": "bin", "op": op, "a": self.expression(node.left), "b": self.expression(node.right)}
        if isinstance(node, ast.UnaryOp):
            op = type(node.op).__name__
            if op not in {"Not", "UAdd", "USub"}:
                self.reject(node)
            # This permits the signed-64-bit minimum without first importing +2**63.
            if op == "USub" and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float):
                return self.literal(-node.operand.value)
            return {"k": "unary", "op": op, "v": self.expression(node.operand)}
        if isinstance(node, ast.BoolOp):
            return {"k": "bool", "op": type(node.op).__name__, "items": [self.expression(x) for x in node.values]}
        if isinstance(node, ast.Compare):
            ops = [type(x).__name__ for x in node.ops]
            if any(op not in COMPARE for op in ops):
                self.reject(node)
            return {"k": "compare", "a": self.expression(node.left), "ops": ops,
                    "items": [self.expression(x) for x in node.comparators]}
        if isinstance(node, ast.IfExp):
            return {"k": "ifexpr", "test": self.expression(node.test),
                    "yes": self.expression(node.body), "no": self.expression(node.orelse)}
        if isinstance(node, ast.Subscript):
            return {"k": "subscript", "v": self.expression(node.value), "key": self.expression(node.slice)}
        if isinstance(node, ast.Call):
            if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
                self.reject(node)
            args = [self.expression(x) for x in node.args]
            if isinstance(node.func, ast.Name):
                if node.func.id in self.locals:
                    self.reject(node)
                target = self.resolve(node.func.id)
                if getattr(type(target), "_is_private_function", False):
                    self.dependencies[target._id] = target
                    return {"k": "call", "name": target._id, "args": args}
                if isinstance(target, _Intrinsic):
                    return {"k": "builtin", "name": target.name, "args": args}
                if node.func.id in BUILTINS and target is getattr(builtins, node.func.id):
                    return {"k": "builtin", "name": node.func.id, "args": args}
                self.reject(node)
            if isinstance(node.func, ast.Attribute) and node.func.attr in METHODS:
                return {"k": "method", "name": node.func.attr,
                        "receiver": self.expression(node.func.value), "args": args}
            self.reject(node)
        if isinstance(node, ast.ListComp):
            if len(node.generators) != 1 or node.generators[0].is_async:
                self.reject(node)
            gen = node.generators[0]
            iterator = self.expression(gen.iter)
            previous = self.locals
            self.locals = self.locals | {
                n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)
            }
            try:
                return {"k": "listcomp", "elt": self.expression(node.elt),
                        "iter": iterator, "target": self.target(gen.target),
                        "ifs": [self.expression(x) for x in gen.ifs]}
            finally:
                self.locals = previous
        self.reject(node)

    def target(self, node):
        if isinstance(node, ast.Name):
            return {"k": "name", "id": node.id}
        if isinstance(node, (ast.Tuple, ast.List)):
            return {"k": "unpack", "items": [self.target(x) for x in node.elts]}
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            return {"k": "subscript", "id": node.value.id, "key": self.expression(node.slice)}
        self.reject(node)

    def block(self, nodes):
        result = []
        for node in nodes:
            if isinstance(node, ast.Return):
                result.append({"k": "return", "v": self.literal(None) if node.value is None else self.expression(node.value)})
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    self.reject(node)
                result.append({"k": "assign", "target": self.target(node.targets[0]), "v": self.expression(node.value)})
            elif isinstance(node, ast.AugAssign):
                if not isinstance(node.target, ast.Name) or type(node.op).__name__ not in BINOPS:
                    self.reject(node)
                result.append({"k": "assign", "target": self.target(node.target),
                               "v": {"k": "bin", "op": type(node.op).__name__,
                                     "a": self.expression(node.target), "b": self.expression(node.value)}})
            elif isinstance(node, ast.Expr):
                # Docstrings are metadata, not runtime work.
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                result.append({"k": "expr", "v": self.expression(node.value)})
            elif isinstance(node, ast.If):
                result.append({"k": "if", "test": self.expression(node.test),
                               "yes": self.block(node.body), "no": self.block(node.orelse)})
            elif isinstance(node, (ast.For, ast.While)):
                self.loop_depth += 1
                body = self.block(node.body)
                self.loop_depth -= 1
                common = {"body": body, "else": self.block(node.orelse)}
                if isinstance(node, ast.For):
                    common.update(k="for", target=self.target(node.target), iter=self.expression(node.iter))
                else:
                    common.update(k="while", test=self.expression(node.test))
                result.append(common)
            elif isinstance(node, (ast.Break, ast.Continue)):
                if not self.loop_depth:
                    self.reject(node)
                result.append({"k": type(node).__name__.lower()})
            elif isinstance(node, ast.Pass):
                result.append({"k": "pass"})
            else:
                self.reject(node)
        return result

    def compile(self):
        program = {"params": self.params, "body": self.block(self.node.body)}
        return json.dumps(program, ensure_ascii=True, separators=(",", ":")), tuple(self.dependencies.values())
