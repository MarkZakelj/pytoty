#!/usr/bin/env python3

from pathlib import Path

import typer

from . import PydanticToTypeScriptConverter

app = typer.Typer(help="Convert Pydantic models to TypeScript interfaces")


@app.command()
def convert(
    input_dir: Path = typer.Argument(..., help="Directory containing Python files with Pydantic models"),
    output_dir: Path = typer.Argument(..., help="Directory to output TypeScript interface files"),
    pattern: str = typer.Option("**/*.py", help="File pattern to match Python files"),
    no_enum: bool = typer.Option(False, "--no-enum", help="Convert enums to Union types instead of TypeScript enums"),
    no_null: bool = typer.Option(False, "--no-null", help="Don't generate | null for optional fields"),
) -> None:
    """Convert Pydantic models from input directory to TypeScript interfaces in output directory."""

    if not input_dir.exists():
        typer.echo(f"Error: Input directory {input_dir} does not exist", err=True)
        raise typer.Exit(1)

    if not input_dir.is_dir():
        typer.echo(f"Error: {input_dir} is not a directory", err=True)
        raise typer.Exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    converter = PydanticToTypeScriptConverter(no_enum=no_enum, no_null=no_null)
    python_files = list(input_dir.glob(pattern))

    if not python_files:
        typer.echo(f"No Python files found matching pattern '{pattern}' in {input_dir}")
        return

    typer.echo(f"Found {len(python_files)} Python files to process")

    total_models = 0
    for py_file in python_files:
        typer.echo(f"Processing {py_file.relative_to(input_dir)}...")

        models = converter.extract_pydantic_models_from_file(py_file)
        if not models:
            typer.echo(f"  No Pydantic models found in {py_file.name}")
            continue

        total_models += len(models)

        # Generate TypeScript content
        ts_content_parts = []

        # Add enums first
        file_key = str(py_file)
        enums = converter.file_enums.get(file_key, [])
        if enums:
            ts_enums = []
            for enum_class in enums:
                enum_ts = converter.convert_enum_to_typescript(enum_class)
                ts_enums.append(enum_ts)
            ts_content_parts.append("\n\n".join(ts_enums))

        # Add interfaces (this will track import usage)
        ts_interfaces = []
        for model in models:
            interface = converter.convert_pydantic_model_to_typescript(model, py_file)
            ts_interfaces.append(interface)
        if ts_interfaces:
            ts_content_parts.append("\n\n".join(ts_interfaces))

        # Add imports after processing models (so we know what's used)
        imports = converter.generate_typescript_imports(py_file)
        if imports:
            ts_content_parts.insert(0, imports.rstrip())

        # Create output file
        relative_path = py_file.relative_to(input_dir)
        ts_file = output_dir / relative_path.with_suffix(".type.ts")
        ts_file.parent.mkdir(parents=True, exist_ok=True)

        # Combine all parts
        ts_content = "\n\n".join(ts_content_parts)
        ts_file.write_text(ts_content)

        enum_count = len(enums)
        interface_count = len(models)
        if enum_count > 0:
            typer.echo(
                f"  Generated {enum_count} enums + {interface_count} interfaces -> {ts_file.relative_to(output_dir)}"
            )
        else:
            typer.echo(f"  Generated {interface_count} interfaces -> {ts_file.relative_to(output_dir)}")

    typer.echo(f"\nConversion complete well! Generated {total_models} TypeScript interfaces in {output_dir}")


if __name__ == "__main__":
    app()
