from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest


REPO_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = REPO_ROOT / "focusproof"
MEDIA_CORE_ROOT = PACKAGE_ROOT / "media_core"
ALLOWED_CORE_IMPORTS = {"__future__", "focusproof.contracts.media_scan", "typing"}
ALLOWED_CORE_IMPORT_PREFIXES = ("focusproof.contracts.media_scan.",)
ALLOWED_CORE_PREFIX = "focusproof.media_core"
ALLOWED_IMAGE_FILES = {
    PurePosixPath("focusproof/api/media_routes.py"),
    PurePosixPath("focusproof/openhands_runtime/runtime_evidence_message_factory.py"),
    PurePosixPath("focusproof/media_projection/image_narrative_provider.py"),
    PurePosixPath("focusproof/openhands_runtime/runtime_contributions.py"),
    PurePosixPath("focusproof/openhands_runtime/tools/media_evidence.py"),
    PurePosixPath("focusproof/openhands_runtime/demo_deterministic_provider.py"),
    PurePosixPath("focusproof/bootstrap/media_composition.py"),
}
ALLOWED_IMAGE_DIR = PurePosixPath("focusproof/media_adapters")
ALLOWED_GENERIC_LLM_VISION_FILES = {
    PurePosixPath("focusproof/config/profiles.py"),
    PurePosixPath("focusproof/openhands_adapter/llm_config.py"),
}
PROTECTED_DUTY_PATHS = (
    PurePosixPath("focusproof/openhands_runtime/manager.py"),
    PurePosixPath("focusproof/domain/scoring.py"),
    PurePosixPath("focusproof/openhands_runtime/capabilities.py"),
    PurePosixPath("focusproof/openhands_runtime/tools/text_evidence.py"),
    PurePosixPath("focusproof/openhands_runtime/tools/url_evidence.py"),
    PurePosixPath("focusproof/monad"),
)
TOKEN_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)")
IMAGE_SIGNAL_TOKENS = {"image", "imagecontent", "vision"}
IMAGE_TOKEN_EXEMPTIONS = {"container_image"}
IMAGE_SEMANTIC_KEYS = {
    "type",
    "kind",
    "mime",
    "mime_type",
    "content_type",
    "media_type",
    "modality",
}


@dataclass(frozen=True)
class ImportRef:
    module: str
    level: int


def _module_name_for_path(path: Path) -> str:
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _normalize_repo_relative_path(path: str) -> PurePosixPath:
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized.startswith("agent-server/"):
        normalized = normalized.removeprefix("agent-server/")
    return PurePosixPath(normalized)


def _resolve_import_ref(current_module: str, ref: ImportRef) -> str | None:
    if ref.level == 0:
        return ref.module or None
    package_parts = current_module.split(".")[:-1]
    climb = ref.level - 1
    if climb > len(package_parts):
        return None
    anchor = package_parts[: len(package_parts) - climb]
    suffix = ref.module.split(".") if ref.module else []
    resolved = [*anchor, *suffix]
    return ".".join(part for part in resolved if part) or None


def _imports(source: str, module: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    dynamic_import_names = {"__import__", "import_module"}
    dynamic_import_modules = {"importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
                if alias.name == "importlib":
                    dynamic_import_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            ref = ImportRef(node.module or "", node.level)
            base = _resolve_import_ref(module, ref)
            if base:
                found.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                member = alias.name if base is None else f"{base}.{alias.name}"
                found.add(member)
                if node.module == "importlib" and alias.name == "import_module":
                    dynamic_import_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Call):
            if _is_exact_dynamic_import_call(node, dynamic_import_names, dynamic_import_modules):
                argument = (
                    node.args[0]
                    if node.args
                    else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "name"),
                        None,
                    )
                )
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found.add(argument.value)
    return found


def _is_exact_dynamic_import_call(
    node: ast.Call,
    dynamic_import_names: set[str],
    dynamic_import_modules: set[str],
) -> bool:
    if not node.args and not any(keyword.arg == "name" for keyword in node.keywords):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in dynamic_import_names
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in dynamic_import_modules
    )


def _is_stdlib_dependency(import_name: str) -> bool:
    return import_name.split(".", 1)[0] in sys.stdlib_module_names


def _assert_allowed_dependency(dependency: str, chain: tuple[str, ...]) -> None:
    if dependency in ALLOWED_CORE_IMPORTS:
        return
    if dependency.startswith(ALLOWED_CORE_IMPORT_PREFIXES):
        return
    if _is_stdlib_dependency(dependency):
        return
    if dependency == ALLOWED_CORE_PREFIX or dependency.startswith(f"{ALLOWED_CORE_PREFIX}."):
        return
    raise AssertionError(" -> ".join((*chain, dependency)))


def assert_graph_boundary(start: str, graph: dict[str, set[str]]) -> None:
    def dfs(module: str, chain: tuple[str, ...], seen: set[str]) -> None:
        for dependency in graph.get(module, set()):
            if (
                dependency.startswith("focusproof.")
                and dependency in graph
                and dependency not in seen
            ):
                dfs(dependency, (*chain, dependency), seen | {dependency})
            _assert_allowed_dependency(dependency, chain)

    dfs(start, (start,), {start})


def _assert_media_core_boundary(module: str, source: str) -> None:
    assert_graph_boundary(module, {module: _imports(source, module)})


def _build_media_core_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in MEDIA_CORE_ROOT.rglob("*.py") if MEDIA_CORE_ROOT.exists() else ():
        module = _module_name_for_path(path)
        graph[module] = _imports(path.read_text(encoding="utf-8"), module)
    return graph


class ImageSignalVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.signals: set[str] = set()

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return
        self.visit(node.value)

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc:
            self._visit_call_without_direct_literals(node.exc)
        if node.cause:
            self.visit(node.cause)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_logger_call(node):
            self.visit(node.func)
            for arg in node.args:
                self._visit_without_direct_literal(arg)
            for keyword in node.keywords:
                self._visit_without_direct_literal(keyword.value)
            return
        self._capture_string_literals(node.args)
        self._capture_keyword_literals(node.keywords)
        self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword)

    def visit_Assign(self, node: ast.Assign) -> None:
        semantic_target = any(
            isinstance(target, ast.Name) and target.id.casefold() in IMAGE_SEMANTIC_KEYS
            for target in node.targets
        )
        self._capture_assignment_discriminators(node.value, semantic_target)
        for target in node.targets:
            self.visit(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            semantic_target = (
                isinstance(node.target, ast.Name)
                and node.target.id.casefold() in IMAGE_SEMANTIC_KEYS
            )
            self._capture_assignment_discriminators(node.value, semantic_target)
        self.visit(node.target)
        if node.value:
            self.visit(node.value)

    def visit_Compare(self, node: ast.Compare) -> None:
        self._capture_string_literals((node.left, *node.comparators))
        self.visit(node.left)
        for comparator in node.comparators:
            self.visit(comparator)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg:
            self._record_identifier(node.arg)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if node.value.value == "image" or node.value.value.startswith("image/"):
                self.signals.add(node.value.value)
        self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_identifier(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_identifier(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_identifier(node.name)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._record_identifier(node.arg)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self._record_identifier(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record_identifier(node.attr)
        self.visit(node.value)

    def _is_logger_call(self, node: ast.Call) -> bool:
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in {"logger", "logging", "log"}
        )

    def _capture_string_literals(self, nodes: Iterable[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == "image" or node.value.startswith("image/"):
                    self.signals.add(node.value)

    def _capture_keyword_literals(self, keywords: Iterable[ast.keyword]) -> None:
        for keyword in keywords:
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                if keyword.value.value == "image" or keyword.value.value.startswith("image/"):
                    self.signals.add(keyword.value.value)

    def _capture_assignment_discriminators(
        self, node: ast.AST, semantic_target: bool = False
    ) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (semantic_target and node.value == "image") or node.value == "image/*":
                self.signals.add(node.value)
            elif semantic_target and node.value.startswith("image/"):
                self.signals.add(node.value)
            return
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.casefold() in IMAGE_SEMANTIC_KEYS
                ):
                    self._capture_assignment_discriminators(value, True)
                elif isinstance(value, (ast.Dict, ast.List, ast.Tuple)):
                    self._capture_assignment_discriminators(value)
            return
        if isinstance(node, (ast.List, ast.Tuple)):
            for child in ast.iter_child_nodes(node):
                self._capture_assignment_discriminators(child)

    def _visit_call_without_direct_literals(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            self.visit(node.func)
            for arg in node.args:
                self._visit_without_direct_literal(arg)
            for keyword in node.keywords:
                self._visit_without_direct_literal(keyword.value)
            return
        self._visit_without_direct_literal(node)

    def _visit_without_direct_literal(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return
        self.visit(node)

    def _record_identifier(self, identifier: str) -> None:
        lowered = identifier.lower()
        if lowered in IMAGE_TOKEN_EXEMPTIONS:
            return
        collapsed = identifier.replace("_", "").casefold()
        if collapsed in IMAGE_SIGNAL_TOKENS:
            self.signals.add(identifier)
            return
        pieces = [piece.lower() for piece in TOKEN_RE.findall(identifier)]
        compact = "".join(pieces)
        if compact in IMAGE_SIGNAL_TOKENS or any(piece in IMAGE_SIGNAL_TOKENS for piece in pieces):
            self.signals.add(identifier)


def _image_identifiers(source: str) -> set[str]:
    visitor = ImageSignalVisitor()
    visitor.visit(ast.parse(source))
    return visitor.signals


def _is_allowed_image_path(path: PurePosixPath) -> bool:
    return path in ALLOWED_IMAGE_FILES or path.is_relative_to(ALLOWED_IMAGE_DIR)


def _is_protected_duty_path(path: PurePosixPath) -> bool:
    return any(
        path == protected or path.is_relative_to(protected) for protected in PROTECTED_DUTY_PATHS
    )


def _assert_image_branch_location(path: str, source: str) -> None:
    normalized = _normalize_repo_relative_path(path)
    identifiers = _image_identifiers(source)
    if normalized in ALLOWED_GENERIC_LLM_VISION_FILES:
        identifiers.discard("supports_vision")
    if identifiers and (
        _is_protected_duty_path(normalized) or not _is_allowed_image_path(normalized)
    ):
        raise AssertionError(
            f"image-specific identifiers {sorted(identifiers)} outside approved adapter: {normalized.as_posix()}"
        )


def test_imports_resolve_import_and_importfrom_variants() -> None:
    source = "\n".join(
        (
            "import sqlalchemy.orm",
            "from focusproof.domain import scoring",
            "from ..domain import scoring",
            "from .. import openhands_runtime",
        )
    )
    imports = _imports(source, "focusproof.media_core.ingestion")
    assert "sqlalchemy.orm" in imports
    assert "focusproof.domain" in imports
    assert "focusproof.domain.scoring" in imports
    assert "focusproof.domain" in imports
    assert "focusproof.domain.scoring" in imports
    assert "focusproof.openhands_runtime" in imports


def test_imports_resolve_exact_dynamic_import_constants() -> None:
    source = "\n".join(
        (
            '__import__("sqlalchemy")',
            'importlib.import_module("sqlalchemy", package="pkg")',
            '__import__("sqlalchemy", globals(), locals(), [], 0)',
            'importlib.import_module(name="sqlalchemy")',
            "importlib.import_module(module_name)",
            "__import__(module_name)",
            "from importlib import import_module as load_module",
            'load_module("sqlalchemy", package="pkg")',
        )
    )
    imports = _imports(source, "focusproof.media_core.ingestion")
    assert "sqlalchemy" in imports
    assert "module_name" not in imports


@pytest.mark.parametrize(
    "statement",
    [
        "__import__(name=module_name)",
        "importlib.import_module(name=module_name)",
        "importlib.import_module(module_name)",
    ],
)
def test_dynamic_imports_reject_non_constant_names(statement: str) -> None:
    assert "module_name" not in _imports(statement, "focusproof.media_core.ingestion")


def test_media_core_allows_only_stdlib_typing_and_media_core() -> None:
    _assert_media_core_boundary(
        "focusproof.media_core.ingestion",
        "from __future__ import annotations\nimport json\nfrom typing import Any\nfrom focusproof.media_core.ports import Store\n",
    )


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("import requests", "focusproof.media_core.ingestion -> requests"),
        ("import numpy", "focusproof.media_core.ingestion -> numpy"),
        (
            "from focusproof.domain import scoring",
            "focusproof.media_core.ingestion -> focusproof.domain",
        ),
    ],
)
def test_media_core_rejects_non_whitelisted_dependencies(statement: str, expected: str) -> None:
    with pytest.raises(AssertionError, match=re.escape(expected)):
        _assert_media_core_boundary("focusproof.media_core.ingestion", f"{statement}\n")


def test_media_core_dfs_rejects_transitive_dependency_paths() -> None:
    graph = {
        "focusproof.media_core.ingestion": {"focusproof.media_core.ports", "focusproof.bridge"},
        "focusproof.media_core.ports": {"typing"},
        "focusproof.bridge": {"sqlalchemy"},
    }
    with pytest.raises(
        AssertionError,
        match=re.escape("focusproof.media_core.ingestion -> focusproof.bridge -> sqlalchemy"),
    ):
        assert_graph_boundary("focusproof.media_core.ingestion", graph)


def test_real_media_core_tree_obeys_import_boundary() -> None:
    graph = _build_media_core_graph()
    for module in graph:
        assert_graph_boundary(module, graph)


def test_image_branches_allow_only_exact_repo_relative_paths() -> None:
    _assert_image_branch_location("focusproof/media_adapters/codec.py", "class ImageCodec: pass")
    _assert_image_branch_location(
        "focusproof/api/media_routes.py",
        "def route(kind: str) -> bool:\n    return kind == 'image'\n",
    )
    _assert_image_branch_location(
        "focusproof/media_projection/image_narrative_provider.py",
        "def emit(payload: str, vision: bool = False) -> str:\n    return payload\n",
    )


@pytest.mark.parametrize(
    "path",
    [
        "focusproof/config/profiles.py",
        "focusproof/openhands_adapter/llm_config.py",
    ],
)
def test_generic_llm_vision_capability_is_allowed_only_in_exact_config_files(path: str) -> None:
    _assert_image_branch_location(path, "supports_vision = False")


@pytest.mark.parametrize(
    "path",
    [
        "focusproof/config/other.py",
        "focusproof/openhands_adapter/other.py",
        "focusproof/openhands_runtime/manager.py",
        "focusproof/domain/scoring.py",
        "focusproof/openhands_runtime/capabilities.py",
        "focusproof/openhands_runtime/tools/text_evidence.py",
        "focusproof/monad/runtime.py",
    ],
)
def test_generic_llm_vision_capability_is_rejected_everywhere_else(path: str) -> None:
    with pytest.raises(AssertionError, match="outside approved adapter"):
        _assert_image_branch_location(path, "supports_vision = False")


@pytest.mark.parametrize(
    "source",
    [
        "supports_vision = False\nmime_type = 'image/png'",
        "supports_vision = False\nclass ImageContent: pass",
        "supports_vision = False\ndef decode_image(): pass",
    ],
)
def test_generic_llm_vision_config_does_not_admit_other_image_signals(source: str) -> None:
    with pytest.raises(AssertionError, match="outside approved adapter"):
        _assert_image_branch_location("focusproof/config/profiles.py", source)


@pytest.fixture
def media_routes_fixture() -> tuple[str, str]:
    return (
        "focusproof/api/media_routes.py",
        "def route(kind: str) -> bool:\n    return kind == 'image'\n",
    )


def test_image_branches_accept_valid_media_routes_fixture(
    media_routes_fixture: tuple[str, str],
) -> None:
    path, source = media_routes_fixture
    _assert_image_branch_location(path, source)


@pytest.mark.parametrize(
    "path",
    [
        "focusproof/domain/scoring_inputs.py",
        "focusproof/not_media_adapters/x.py",
        "focusproof/api/media_routes.py.evil",
        "focusproof/openhands_runtime/manager.py",
        "focusproof/domain/scoring.py",
        "focusproof/monad/runtime.py",
        "focusproof/openhands_runtime/tools/text_evidence.py",
        "focusproof/openhands_runtime/tools/url_evidence.py",
    ],
)
def test_image_branches_reject_disallowed_and_protected_paths(path: str) -> None:
    with pytest.raises(AssertionError, match="outside approved adapter"):
        _assert_image_branch_location(path, "def select_image(kind='image'): return kind")


def test_image_identifier_detection_ignores_comments_docstrings_logger_and_raise_messages() -> None:
    source = "\n".join(
        (
            '"""ImageContent is documentation only."""',
            "def ok(container_image: str) -> None:",
            "    logger.info('image')",
            "    raise ValueError('image/png')",
            "    plain = 'image/png'",
            "    return None",
            "# vision image",
        )
    )
    assert _image_identifiers(source) == set()


def test_image_identifier_detection_keeps_nested_logger_and_raise_calls() -> None:
    source = "\n".join(
        (
            "logger.info(process_image())",
            "raise Error(select_image())",
        )
    )
    assert _image_identifiers(source) >= {"process_image", "select_image"}


def test_image_identifier_detection_catches_exact_discriminators_in_containers() -> None:
    source = "\n".join(
        (
            "kind = 'image/png'",
            "formats = {'type': 'image/*', 'content_type': 'image/png'}",
            "payload = {'content_type': 'image/png'}",
            "note = 'image/png'",
        )
    )
    identifiers = _image_identifiers(source)
    assert {"image/png", "image/*"} <= identifiers
    assert "note" not in identifiers


def test_image_identifier_detection_catches_semantic_assignment_targets_only() -> None:
    source = "\n".join(
        (
            "content_type = 'image/png'",
            "note = 'image/png'",
            "payload: dict = {'media_type': 'image'}",
        )
    )
    identifiers = _image_identifiers(source)
    assert {"image/png", "image"} <= identifiers
    assert "note" not in identifiers


def test_image_identifier_detection_catches_identifiers_and_contextual_strings() -> None:
    source = "\n".join(
        (
            "def route(kind: str, image: str, vision: bool) -> None:",
            "    if kind == 'image':",
            "        send(image=image, mime='image/png')",
            "    content = ImageContent(image)",
            "    if vision:",
            "        return content",
            "    container_image = 'image/png'",
            "    note = 'image/png'",
        )
    )
    identifiers = _image_identifiers(source)
    assert "image" in identifiers
    assert "image/png" in identifiers
    assert "ImageContent" in identifiers
    assert "vision" in identifiers
    assert "container_image" not in identifiers


def test_focusproof_tree_confines_image_specific_identifiers() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        _assert_image_branch_location(
            path.relative_to(REPO_ROOT).as_posix(),
            path.read_text(encoding="utf-8"),
        )


def test_malware_scanning_duties_do_not_enter_protected_surfaces() -> None:
    protected_roots = (
        PACKAGE_ROOT / "openhands_runtime",
        PACKAGE_ROOT / "openhands_adapter",
        PACKAGE_ROOT / "domain" / "plugins",
        PACKAGE_ROOT / "monad",
    )
    forbidden = ("malware", "clamd", "malwarescanner", "malwarescanverdict")
    violations: list[str] = []
    for root in protected_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8").casefold()
            if any(token in source for token in forbidden):
                violations.append(str(path.relative_to(REPO_ROOT)))
    assert violations == []
