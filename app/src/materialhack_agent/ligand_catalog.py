from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LigandModelRef:
    metal: str
    entry_name: str
    uri: str
    source_id: str


_TARGET_TO_FOLDER = {
    "ZN": "Zn",
    "ZN2": "Zn",
    "ZN2+": "Zn",
    "NI": "Ni",
    "NI2": "Ni",
    "NI2+": "Ni",
    "MG": "Mg",
    "MG2": "Mg",
    "MG2+": "Mg",
    "MN": "Mn",
    "MN2": "Mn",
    "MN2+": "Mn",
    "NA": "Na",
    "NA+": "Na",
    "CU": "Cu",
    "CU2": "Cu",
    "CU2+": "Cu",
    "CA": "Ca",
    "CA2": "Ca",
    "CA2+": "Ca",
    "CO": "Co",
    "CO2": "Co",
    "CO2+": "Co",
    "FE": "Fe",
    "FE2": "Fe",
    "FE2+": "Fe",
    "FE3": "Fe",
    "FE3+": "Fe",
    "K": "K",
    "K+": "K",
}


def select_ligand_model(target: str, *, archive_path: str | Path | None, index: int) -> LigandModelRef | None:
    """Pick a deterministic ligand model from the local CCDC/CSD ligand archive."""

    if archive_path is None:
        return None

    archive = Path(archive_path)
    if not archive.exists():
        return None

    metal = _target_to_folder(target)
    if metal is None:
        return None

    prefix = f"ligands_10000/{metal}/"
    try:
        with zipfile.ZipFile(archive) as ligand_zip:
            entries = sorted(
                name
                for name in ligand_zip.namelist()
                if name.startswith(prefix) and name.endswith(".mol2") and "__MACOSX" not in name
            )
    except zipfile.BadZipFile:
        return None

    if not entries:
        return None

    entry_name = entries[index % len(entries)]
    source_id = Path(entry_name).stem
    return LigandModelRef(
        metal=metal,
        entry_name=entry_name,
        uri=f"zip://{archive.name}!/{entry_name}",
        source_id=f"{metal}:{source_id}",
    )


def _target_to_folder(target: str) -> str | None:
    normalized = re.sub(r"[^A-Z0-9+]", "", target.upper())
    return _TARGET_TO_FOLDER.get(normalized)
