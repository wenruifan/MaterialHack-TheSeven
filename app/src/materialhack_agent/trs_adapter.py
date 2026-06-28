from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from trs import ProteinStructure, calculate_trs


DONOR_RESIDUES = frozenset({"C", "D", "E", "H", "N", "Q", "S", "T", "Y"})
HYDROPHOBIC_RESIDUES = frozenset({"A", "I", "L", "M", "F", "W", "V"})


@dataclass(frozen=True)
class TrsSequenceResult:
    total: float
    raw_total: float
    components: dict[str, float]
    weights: dict[str, float]
    input_mode: str
    contact_residue_indices: tuple[int, ...]
    metal_node: str


def score_sequence_trs(
    sequence: str,
    *,
    target: str | None = None,
    changed_positions: tuple[int, ...] = (),
) -> TrsSequenceResult:
    """Score a sequence with the real TRS graph API.

    Until Boltz/TRS structure adapters provide before/after atom tables, this
    builds a deterministic residue-contact graph from the sequence and adds a
    target metal node at likely donor residues. The important contract is that
    the loop memory stores a real TRS result shape: total, components, weights,
    and the assumptions used to produce it.
    """

    if not sequence:
        raise ValueError("TRS scoring requires a non-empty sequence")

    before = _sequence_structure(sequence)
    contacts = _metal_contact_indices(sequence, changed_positions=changed_positions)
    after = _sequence_structure(
        sequence,
        metal_node=_metal_node(target),
        metal_contact_indices=contacts,
    )
    result = calculate_trs(before, after, default_ideal_angle=109.5)
    raw_total = float(result.total)
    normalization = max(10.0, len(sequence) * 4.0)
    normalized_total = raw_total / (raw_total + normalization) if raw_total > 0 else 0.0
    return TrsSequenceResult(
        total=round(normalized_total, 3),
        raw_total=round(raw_total, 6),
        components={key: round(float(value), 6) for key, value in asdict(result.components).items()},
        weights={key: float(value) for key, value in result.weights.items()},
        input_mode="sequence_contact_graph",
        contact_residue_indices=contacts,
        metal_node=_metal_node(target),
    )


def changed_positions_from_diff(machine_diff: str | None) -> tuple[int, ...]:
    if not machine_diff:
        return ()
    return tuple(int(match) for match in re.findall(r"\d+", machine_diff))


def _sequence_structure(
    sequence: str,
    *,
    metal_node: str | None = None,
    metal_contact_indices: tuple[int, ...] = (),
) -> ProteinStructure:
    nodes = [f"R{index}:{residue}" for index, residue in enumerate(sequence, start=1)]
    coordinates = {
        node: (float(index), 0.0, _residue_height(sequence[index - 1]))
        for index, node in enumerate(nodes, start=1)
    }
    edges: set[tuple[str, str]] = set()
    for index in range(len(nodes) - 1):
        edges.add((nodes[index], nodes[index + 1]))
    for index in range(len(nodes) - 3):
        if sequence[index] in HYDROPHOBIC_RESIDUES and sequence[index + 3] in HYDROPHOBIC_RESIDUES:
            edges.add((nodes[index], nodes[index + 3]))

    metal_nodes: tuple[str, ...] = ()
    if metal_node is not None:
        metal_nodes = (metal_node,)
        coordinates[metal_node] = _metal_coordinate(coordinates, metal_contact_indices)
        for residue_index in metal_contact_indices:
            if 1 <= residue_index <= len(nodes):
                edges.add((nodes[residue_index - 1], metal_node))

    return ProteinStructure.from_edges(
        nodes=(*nodes, *metal_nodes),
        edges=edges,
        coordinates=coordinates,
        metal_nodes=metal_nodes,
    )


def _metal_contact_indices(sequence: str, *, changed_positions: tuple[int, ...]) -> tuple[int, ...]:
    donors = tuple(index for index, residue in enumerate(sequence, start=1) if residue in DONOR_RESIDUES)
    preferred = tuple(index for index in changed_positions if index in donors)
    contacts: list[int] = list(preferred[:2])
    for index in donors:
        if index not in contacts:
            contacts.append(index)
        if len(contacts) >= 4:
            break
    if contacts:
        return tuple(sorted(contacts))
    midpoint = max(1, len(sequence) // 2)
    return (midpoint,)


def _metal_node(target: str | None) -> str:
    normalized = (target or "metal").upper()
    if normalized.startswith("ZN"):
        return "ZN"
    if normalized.startswith("CA"):
        return "CA"
    if normalized.startswith("MG"):
        return "MG"
    if normalized.startswith("FE"):
        return "FE"
    if normalized.startswith("CU"):
        return "CU"
    if normalized.startswith("NI"):
        return "NI"
    if normalized.startswith("MN"):
        return "MN"
    return "METAL"


def _residue_height(residue: str) -> float:
    if residue in DONOR_RESIDUES:
        return 1.0
    if residue in HYDROPHOBIC_RESIDUES:
        return -0.5
    return 0.0


def _metal_coordinate(
    coordinates: dict[str, tuple[float, float, float]],
    contact_indices: tuple[int, ...],
) -> tuple[float, float, float]:
    if not contact_indices:
        return (0.0, 2.4, 1.0)
    contact_points = [
        coordinates[node]
        for node in coordinates
        if node.startswith("R") and int(node.split(":", maxsplit=1)[0][1:]) in contact_indices
    ]
    if not contact_points:
        return (0.0, 2.4, 1.0)
    x = sum(point[0] for point in contact_points) / len(contact_points)
    z = sum(point[2] for point in contact_points) / len(contact_points)
    return (x, 2.4, z)
