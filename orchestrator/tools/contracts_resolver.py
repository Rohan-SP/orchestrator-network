"""Resolves ordered contract references to their selected clause fields."""

# kjl2: test prefix formatting with multiple contract references, including
# references from different contract files, and verify field selection and order.

import yaml

from ..config import configured_path


def resolve_contracts(entries: list[str | dict]) -> list[dict]:
    contracts = {}
    resolved = []

    for entry in entries:
        if isinstance(entry, str):
            ref = entry
            fields = ["statement"]
        else:
            ref = entry["ref"]
            fields = entry.get("fields", ["statement"])

        contract_name, clause_id = ref.split(":")
        if contract_name not in contracts:
            path = configured_path("contracts") / f"{contract_name}.yaml"
            contracts[contract_name] = yaml.safe_load(path.read_text(encoding="utf-8"))

        clause = next(
            clause
            for clause in contracts[contract_name]["clauses"]
            if clause["id"] == clause_id
        )
        resolved.append({
            "ref": ref,
            **{field: clause[field] for field in fields},
        })

    return resolved
