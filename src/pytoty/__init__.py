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
    def __init__(self):
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

        # Track imports for TypeScript generation
        self.model_imports: Dict[str, Set[str]] = {}

        # Track enums for TypeScript generation
        self.file_enums: Dict[str, List[Type[Enum]]] = {}

    def python_type_to_typescript(self, python_type: Any) -> str:
        """Convert a Python type to its TypeScript equivalent."""
        if python_type in self.type_mapping:
            return self.type_mapping[python_type]

        origin = get_origin(python_type)
        args = get_args(python_type)

        if origin is list:
            if args:
                item_type = self.python_type_to_typescript(args[0])
                return f"{item_type}[]"
            return "any[]"

        if origin is dict:
            if len(args) == 2:
                key_type = self.python_type_to_typescript(args[0])
                value_type = self.python_type_to_typescript(args[1])
                return f"Record<{key_type}, {value_type}>"
            return "Record<string, any>"

        # Handle Union types (both typing.Union and Python 3.10+ | syntax)
        if origin is Union or origin is types.UnionType:
            if len(args) == 2 and type(None) in args:
                non_null_type = args[0] if args[1] is type(None) else args[1]
                return f"{self.python_type_to_typescript(non_null_type)} | null"
            return " | ".join(self.python_type_to_typescript(arg) for arg in args)

        # Handle string-based type annotations (e.g., "EmailStr")
        if isinstance(python_type, str):
            if python_type == "EmailStr":
                return "string"
            return python_type.lower() if python_type.lower() in ["string", "number", "boolean"] else "any"

        if hasattr(python_type, "__name__"):
            # Handle specific Pydantic types by name
            if python_type.__name__ == "EmailStr":
                return "string"
            return python_type.__name__

        return "any"

    def extract_same_directory_imports(self, file_path: Path) -> Set[str]:
        """Extract imports from the same directory."""
        imports = set()

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith(file_path.parent.name + "."):
                        # Extract the module name after the directory
                        module_parts = node.module.split(".")
                        if len(module_parts) >= 2:
                            module_name = module_parts[-1]  # Last part is the file name
                            for alias in node.names:
                                imports.add((module_name, alias.name))
        except Exception as e:
            print(f"Error parsing imports from {file_path}: {e}")

        return imports

    def extract_pydantic_models_from_file(self, file_path: Path) -> List[Type[BaseModel]]:
        """Extract Pydantic models from a Python file."""
        models = []

        # Store imports for this file
        file_key = str(file_path)
        self.model_imports[file_key] = self.extract_same_directory_imports(file_path)

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

    def convert_enum_to_typescript(self, enum_class: Type[Enum]) -> str:
        """Convert a Python Enum to a TypeScript enum."""
        enum_name = enum_class.__name__
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

    def convert_pydantic_model_to_typescript(self, model: Type[BaseModel]) -> str:
        """Convert a Pydantic model to a TypeScript interface."""
        interface_name = model.__name__
        fields = []

        for field_name, field_info in model.model_fields.items():
            field_type = field_info.annotation if field_info.annotation else Any
            ts_type = self.python_type_to_typescript(field_type)

            is_optional = not field_info.is_required()
            optional_marker = "?" if is_optional else ""

            fields.append(f"  {field_name}{optional_marker}: {ts_type};")

        fields_str = "\n".join(fields)
        return f"export interface {interface_name} {{\n{fields_str}\n}}"

    def generate_typescript_imports(self, file_path: Path) -> str:
        """Generate TypeScript import statements for same-directory dependencies."""
        file_key = str(file_path)
        imports = self.model_imports.get(file_key, set())

        if not imports:
            return ""

        import_statements = []
        # Group imports by module
        imports_by_module = {}
        for module_name, imported_name in imports:
            if module_name not in imports_by_module:
                imports_by_module[module_name] = []
            imports_by_module[module_name].append(imported_name)

        for module_name, imported_names in imports_by_module.items():
            imported_names_str = ", ".join(imported_names)
            import_statements.append(f"import {{ {imported_names_str} }} from './{module_name}';")

        return "\n".join(import_statements) + "\n\n" if import_statements else ""
