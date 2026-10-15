#!/usr/bin/env python3
"""
Validate a GFA manifest (v1) - check schema + file existence.

Usage:
    python validate_manifest.py <manifest.json>
    python validate_manifest.py --help
"""
# 验证 Identify manifest
#python apps/cli/validate_manifest.py workspace/20260128_072702_3148145_fb521a/final/Ft.SPL.identify.manifest.json

# 如果想指定其他 schema 文件
#python apps/cli/validate_manifest.py workspace/.../some.manifest.json --schema gfa_core/schemas/core/manifest_v2.schema.json

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    print("⚠️  jsonschema not installed, schema validation will be skipped.")
    print("   Install with: pip install jsonschema")


def validate_schema(manifest_path: Path, schema_path: Path):
    """Validate manifest against JSON Schema."""
    if not HAS_JSONSCHEMA:
        print("❌ Schema validation unavailable: jsonschema is not installed")
        return False

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            instance = json.load(f)
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        jsonschema.validate(instance=instance, schema=schema)
        print(f"✅ Schema validation passed")
        return True
    except json.JSONDecodeError as e:
        print(f"❌ Manifest is not valid JSON: {e}")
        return False
    except jsonschema.exceptions.ValidationError as e:
        print(f"❌ Schema validation failed:")
        print(f"   Path: {e.json_path}")
        print(f"   Error: {e.message}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error during schema validation: {e}")
        return False


def validate_file_existence(manifest_path: Path):
    """Check that all files referenced in manifest.artifacts exist."""
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    artifacts = manifest.get('artifacts', {})
    if not artifacts:
        print("⚠️  No artifacts found in manifest")
        return True

    workspace_root = manifest.get('workspace_root')
    if not workspace_root:
        print("⚠️  workspace_root not found in manifest, cannot resolve relative paths")
        # We'll still try using full_path if available

    all_exist = True
    missing_files = []

    for artifact_name, artifact_info in artifacts.items():
        # Try full_path first, then path/relpath_or_hint
        full_path = artifact_info.get('full_path')
        if not full_path:
            # Try to construct from workspace_root + path/relpath
            rel_path = artifact_info.get('path') or artifact_info.get('relpath_or_hint')
            if rel_path and workspace_root:
                full_path = os.path.join(workspace_root, rel_path)
            else:
                print(f"⚠️  Artifact '{artifact_name}' has no full_path or resolvable path")
                continue

        file_path = Path(full_path)
        if file_path.exists():
            print(f"✅ [{artifact_name}] file exists: {file_path}")
        else:
            print(f"❌ [{artifact_name}] file MISSING: {file_path}")
            missing_files.append(f"{artifact_name}: {file_path}")
            all_exist = False

    if missing_files:
        print("\n❌ Missing files summary:")
        for m in missing_files:
            print(f"   - {m}")

    return all_exist


def main():
    parser = argparse.ArgumentParser(description="Validate GFA manifest")
    parser.add_argument("manifest", help="Path to manifest.json")
    parser.add_argument("--schema", 
                       default="gfa_core/schemas/core/manifest_v1.schema.json",
                       help="Path to JSON schema file (default: gfa_core/schemas/core/manifest_v1.schema.json)")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"❌ Manifest file not found: {manifest_path}")
        sys.exit(1)

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"❌ Schema file not found: {schema_path}")
        sys.exit(1)

    print(f"🔍 Validating manifest: {manifest_path}")
    print(f"   Using schema: {schema_path}")

    # Step 1: Schema validation
    schema_ok = validate_schema(manifest_path, schema_path)
    
    # Step 2: File existence validation
    files_ok = validate_file_existence(manifest_path)
    
    # Summary
    print("\n" + "="*60)
    if schema_ok and files_ok:
        print("✅ Manifest validation PASSED")
        sys.exit(0)
    else:
        print("❌ Manifest validation FAILED")
        if not schema_ok:
            print("   - Schema validation failed")
        if not files_ok:
            print("   - File existence check failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
