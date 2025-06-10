import ast
import importlib.util
import inspect
import types
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Set, Type, Union, get_args, get_origin

from pydantic import BaseModel

try:
    from pydantic import EmailStr
except ImportError:
    EmailStr = None


def hello() -> str:
    return "Hello from pydantic-to-typescript!"


__all__ = ["PydanticToTypeScriptConverter", "hello"]


class PydanticToTypeScriptConverter:
    def __init__(self, no_enum: bool = False, no_null: bool = False):
        self.type_mapping = {
            str: "string",
            int: "number",
            float: "number",
            bool: "boolean",
            list: "Array",
            dict: "Record<string, any>",
            type(None): "null",
            datetime: "string",
            date: "string",
        }

        # Add EmailStr if available
        if EmailStr is not None:
            self.type_mapping[EmailStr] = "string"

        # Track enums for TypeScript generation
        self.file_enums: Dict[str, List[Type[Enum]]] = {}

        # Track available Pydantic imports per file
        self.file_pydantic_imports: Dict[str, Dict[str, str]] = {}

        # Track which imports are actually used in models
        self.used_imports: Dict[str, Set[str]] = {}

        # Configuration for enum handling
        self.no_enum = no_enum

        # Configuration for null handling
        self.no_null = no_null

    def python_type_to_typescript(self, python_type: Any, current_file: Path = None) -> str:
        """Convert a Python type to its TypeScript equivalent."""
        if python_type in self.type_mapping:
            return self.type_mapping[python_type]

        origin = get_origin(python_type)
        args = get_args(python_type)

        if origin is list:
            if args:
                item_type = self.python_type_to_typescript(args[0], current_file)
                return f"{item_type}[]"
            return "any[]"

        if origin is dict:
            if len(args) == 2:
                key_type = self.python_type_to_typescript(args[0], current_file)
                value_type = self.python_type_to_typescript(args[1], current_file)
                return f"Record<{key_type}, {value_type}>"
            return "Record<string, any>"

        # Handle Union types (both typing.Union and Python 3.10+ | syntax)
        if origin is Union or origin is types.UnionType:
            if len(args) == 2 and type(None) in args:
                non_null_type = args[0] if args[1] is type(None) else args[1]
                ts_type = self.python_type_to_typescript(non_null_type, current_file)
                if self.no_null:
                    return ts_type
                return f"{ts_type} | null"
            return " | ".join(self.python_type_to_typescript(arg, current_file) for arg in args)

        # Handle string-based type annotations (e.g., "EmailStr")
        if isinstance(python_type, str):
            if python_type == "EmailStr":
                return "string"
            # Check if this is a Pydantic model reference from imports
            if current_file:
                file_key = str(current_file)
                available_imports = self.file_pydantic_imports.get(file_key, {})
                if python_type in available_imports:
                    # Track that this import is used
                    if file_key not in self.used_imports:
                        self.used_imports[file_key] = set()
                    self.used_imports[file_key].add(python_type)
                    return python_type
            return python_type.lower() if python_type.lower() in ["string", "number", "boolean"] else "any"

        if hasattr(python_type, "__name__"):
            # Handle specific Pydantic types by name
            if python_type.__name__ == "EmailStr":
                return "string"
            # Check if this is a Pydantic model class
            if inspect.isclass(python_type) and issubclass(python_type, BaseModel):
                if current_file:
                    file_key = str(current_file)
                    # Check if this model is imported
                    available_imports = self.file_pydantic_imports.get(file_key, {})
                    if python_type.__name__ in available_imports:
                        # Track that this import is used
                        if file_key not in self.used_imports:
                            self.used_imports[file_key] = set()
                        self.used_imports[file_key].add(python_type.__name__)
                return python_type.__name__
            return python_type.__name__

        return "any"

    def extract_pydantic_imports(self, file_path: Path) -> Dict[str, str]:
        """Extract imports that could be Pydantic models."""
        pydantic_imports = {}  # {imported_name: module_path}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module:
                        # Handle relative imports (e.g., from .module import something)
                        if node.level > 0:
                            module_name = node.module
                            for alias in node.names:
                                pydantic_imports[alias.name] = module_name
                        # Handle absolute imports that might be Pydantic models
                        else:
                            module_parts = node.module.split(".")
                            # Check if this looks like it could contain Pydantic models
                            if any(part in ["schemas", "models", "entities"] for part in module_parts):
                                module_name = module_parts[-1]  # Last part is the file name
                                for alias in node.names:
                                    pydantic_imports[alias.name] = module_name
                            # Handle same-directory imports
                            elif node.module.startswith(file_path.parent.name + "."):
                                module_name = module_parts[-1]
                                for alias in node.names:
                                    pydantic_imports[alias.name] = module_name
                            # Handle direct relative imports within the same package
                            elif "." not in node.module:
                                potential_file = file_path.parent / f"{node.module}.py"
                                if potential_file.exists():
                                    for alias in node.names:
                                        pydantic_imports[alias.name] = node.module

        except Exception as e:
            print(f"Error parsing imports from {file_path}: {e}")

        return pydantic_imports

    def extract_pydantic_models_from_file(self, file_path: Path) -> List[Type[BaseModel]]:
        """Extract Pydantic models from a Python file."""
        models = []

        # Store Pydantic imports for this file
        file_key = str(file_path)
        self.file_pydantic_imports[file_key] = self.extract_pydantic_imports(file_path)

        # Also extract and store enums for this file
        self.file_enums[file_key] = self.extract_enums_from_file(file_path)

        # Get the models defined in this specific file using AST
        defined_classes = self._get_defined_classes(file_path)

        try:
            spec = importlib.util.spec_from_file_location("module", file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for name in dir(module):
                    if name in defined_classes:  # Only include classes defined in this file
                        obj = getattr(module, name)
                        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj != BaseModel:
                            models.append(obj)
        except Exception as e:
            print(f"Error processing {file_path}: {e}")

        return models

    def _get_defined_classes(self, file_path: Path) -> Set[str]:
        """Get class names that are defined in this file (not imported)."""
        defined_classes = set()

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    defined_classes.add(node.name)
        except Exception as e:
            print(f"Error parsing classes from {file_path}: {e}")

        return defined_classes

    def extract_enums_from_file(self, file_path: Path) -> List[Type[Enum]]:
        """Extract Enum classes from a Python file."""
        enums = []

        # Get the enum classes defined in this specific file using AST
        defined_classes = self._get_defined_classes(file_path)

        try:
            spec = importlib.util.spec_from_file_location("module", file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for name in dir(module):
                    if name in defined_classes:  # Only include classes defined in this file
                        obj = getattr(module, name)
                        if inspect.isclass(obj) and issubclass(obj, Enum) and obj != Enum:
                            enums.append(obj)
        except Exception as e:
            print(f"Error processing enums from {file_path}: {e}")

        return enums

    def convert_enum_to_typescript(self, enum_class: Type[Enum], no_enum: bool = False) -> str:
        """Convert a Python Enum to a TypeScript enum or union type."""
        enum_name = enum_class.__name__

        if no_enum or self.no_enum:
            # Convert to union type instead of enum
            union_values = []
            for member in enum_class:
                if isinstance(member.value, str):
                    union_values.append(f'"{member.value}"')
                else:
                    union_values.append(str(member.value))
            union_type = " | ".join(union_values)
            return f"export type {enum_name} = {union_type};"
        else:
            # Traditional enum
            enum_values = []
            for member in enum_class:
                if isinstance(member.value, str):
                    # String enum values need quotes
                    enum_values.append(f'  {member.name} = "{member.value}",')
                else:
                    # Numeric enum values don't need quotes
                    enum_values.append(f"  {member.name} = {member.value},")

            enum_values_str = "\n".join(enum_values)
            return f"export enum {enum_name} {{\n{enum_values_str}\n}}"

    def convert_pydantic_model_to_typescript(self, model: Type[BaseModel], current_file: Path = None) -> str:
        """Convert a Pydantic model to a TypeScript interface."""
        interface_name = model.__name__
        fields = []

        for field_name, field_info in model.model_fields.items():
            field_type = field_info.annotation if field_info.annotation else Any
            ts_type = self.python_type_to_typescript(field_type, current_file)

            is_optional = not field_info.is_required()
            optional_marker = "?" if is_optional else ""

            fields.append(f"  {field_name}{optional_marker}: {ts_type};")

        fields_str = "\n".join(fields)
        return f"export interface {interface_name} {{\n{fields_str}\n}}"

    def generate_typescript_imports(self, file_path: Path) -> str:
        """Generate TypeScript import statements for used Pydantic model dependencies."""
        file_key = str(file_path)
        used_models = self.used_imports.get(file_key, set())
        available_imports = self.file_pydantic_imports.get(file_key, {})

        if not used_models:
            return ""

        import_statements = []
        # Group imports by module
        imports_by_module = {}

        for model_name in used_models:
            if model_name in available_imports:
                module_name = available_imports[model_name]
                if module_name not in imports_by_module:
                    imports_by_module[module_name] = []
                imports_by_module[module_name].append(model_name)

        for module_name, imported_names in imports_by_module.items():
            imported_names_str = ", ".join(imported_names)
            import_statements.append(f"import type {{ {imported_names_str} }} from './{module_name}.type';")

        return "\n".join(import_statements) + "\n\n" if import_statements else ""
