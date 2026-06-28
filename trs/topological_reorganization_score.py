"""Topological Reorganization Score for protein-metal binding.

The public function, ``calculate_trs``, compares a protein structure before metal
binding with a protein/metal structure after binding. Nodes are residue identifiers
plus optional metal identifiers. Edges are undirected contacts/interactions.
Coordinates are optional but enable geometry-based terms. Coordinates do not
create connectivity by themselves: if two nodes have no declared interaction,
there is no edge between them.
"""

# VERY IMPORTANT: We need approximate Distance cutoffs (in angstroms) for metals connects. For now, arbitraty data is used.
# Distance cuttoffs means: if two atoms are closer than this distance, count them as interacting/connected.


from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields
from itertools import combinations
from math import acos, degrees, dist
from typing import Iterable, Mapping, Sequence

import numpy as np

NodeId = str | int
Edge = tuple[NodeId, NodeId]
Coordinate = tuple[float, float, float]


@dataclass(frozen=True)
class AtomRecord:
    """One atom line from the CCDC-style coordinate table."""

    atom_id: int
    atom_name: str
    x: float
    y: float
    z: float
    atom_type: str
    residue_index: int
    residue_name: str
    charge: float = 0.0

    @property
    def coordinate(self) -> Coordinate:
        return (self.x, self.y, self.z)

    @property
    def node_id(self) -> str:
        return f"{self.residue_name}:{self.atom_name}:{self.atom_id}"

    @property
    def element(self) -> str:
        atom_type_root = self.atom_type.split(".", maxsplit=1)[0]
        letters = "".join(char for char in atom_type_root if char.isalpha())
        if not letters:
            letters = "".join(char for char in self.atom_name if char.isalpha())
        if not letters:
            return ""
        return letters[0].upper() + letters[1:].lower()


@dataclass(frozen=True)
class AtomStructure:
    """3D atom structure parsed from the coordinate table."""

    atoms: tuple[AtomRecord, ...]

    @classmethod
    def from_table(cls, text: str) -> "AtomStructure":
        atoms: list[AtomRecord] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) < 8:
                raise ValueError(f"Cannot parse atom row: {line!r}")
            atoms.append(
                AtomRecord(
                    atom_id=int(parts[0]),
                    atom_name=parts[1],
                    x=float(parts[2]),
                    y=float(parts[3]),
                    z=float(parts[4]),
                    atom_type=parts[5],
                    residue_index=int(parts[6]),
                    residue_name=parts[7],
                    charge=float(parts[8]) if len(parts) > 8 else 0.0,
                )
            )
        return cls(tuple(atoms))


@dataclass(frozen=True)
class ProteinStructure:
    """
    Args:
        nodes: All node identifiers in the structure.
        edges: Declared node-node interactions. Residue-metal interactions are
            valid. If two nodes are not listed here, they are treated as
            disconnected even if both have 3D coordinates.
        coordinates: Optional 3D coordinates for residue and/or metal nodes.
        metal_nodes: Nodes that represent metals. These are excluded from N (N is only for amino acids).
    """

    nodes: frozenset[NodeId]
    edges: frozenset[Edge]
    coordinates: Mapping[NodeId, Coordinate] | None = None
    metal_nodes: frozenset[NodeId] = frozenset()

    @classmethod
    def from_edges(
        cls,
        edges: Iterable[Edge],
        nodes: Iterable[NodeId] | None = None,
        coordinates: Mapping[NodeId, Coordinate] | None = None,
        metal_nodes: Iterable[NodeId] | None = None,
    ) -> "ProteinStructure":
        normalized_edges = {_normalize_edge(a, b) for a, b in edges if a != b}
        inferred_nodes = set(nodes or [])
        for a, b in normalized_edges:
            inferred_nodes.add(a)
            inferred_nodes.add(b)
        if coordinates:
            inferred_nodes.update(coordinates)
        metal_set = frozenset(metal_nodes or [])
        inferred_nodes.update(metal_set)
        return cls(
            nodes=frozenset(inferred_nodes),
            edges=frozenset(normalized_edges),
            coordinates=coordinates or {},
            metal_nodes=metal_set,
        )

    @property
    def residue_nodes(self) -> frozenset[NodeId]:
        return self.nodes - self.metal_nodes

    def neighbors(self) -> dict[NodeId, set[NodeId]]:
        adjacency: dict[NodeId, set[NodeId]] = {node: set() for node in self.nodes}
        for a, b in self.edges:
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
        return adjacency

    def has_edge(self, a: NodeId, b: NodeId) -> bool:
        return _normalize_edge(a, b) in self.edges


@dataclass(frozen=True)
class TRSComponents:
    """Individual TRS terms before weighting."""

    degree_change: float
    clustering_change: float
    coordination_number: float
    adjacency_change: float
    triangle_change: float
    path_length_change: float
    lambda2_change: float
    spectrum_change: float
    laplacian_change: float
    betweenness_change: float
    diameter_change: float
    edge_distance_change: float
    geometry_angle_penalty: float
    metal_betweenness: float


@dataclass(frozen=True)
class TRSResult:
    """Weighted TRS result."""

    total: float
    components: TRSComponents
    weights: Mapping[str, float]


def calculate_trs(
    before: ProteinStructure,
    after: ProteinStructure,
    weights: Mapping[str, float] | None = None,
    ideal_metal_angles: Mapping[NodeId, float] | None = None,
    default_ideal_angle: float | None = None,
    disconnected_distance: float | None = None,
) -> TRSResult:
    """Calculate TRS(P, M) = f(S(P), S(P, M)).

    ``before`` should contain the protein residues before binding. ``after``
    should contain the same residues plus optional metal nodes and metal-residue
    contacts. Missing weights default to 1.0.
    """

    residue_nodes = _shared_residue_nodes(before, after)
    if not residue_nodes:
        raise ValueError("TRS requires at least one residue node shared by both structures.")

    before_neighbors = before.neighbors()
    after_neighbors = after.neighbors()
    before_laplacian = _laplacian(before, residue_nodes)
    after_laplacian = _laplacian(after, residue_nodes)
    before_eigenvalues = np.linalg.eigvalsh(before_laplacian)
    after_eigenvalues = np.linalg.eigvalsh(after_laplacian)
    lambda2_before = _lambda2(before_eigenvalues)
    lambda2_after = _lambda2(after_eigenvalues)

    components = TRSComponents(
        degree_change=_mean_abs_difference(
            {node: len(before_neighbors.get(node, set())) for node in residue_nodes},
            {node: len(after_neighbors.get(node, set())) for node in residue_nodes},
            residue_nodes,
        ),
        clustering_change=_mean_abs_difference(
            _clustering_coefficients(before, residue_nodes),
            _clustering_coefficients(after, residue_nodes),
            residue_nodes,
        ),
        coordination_number=float(_coordination_number(after)),
        adjacency_change=float(
            np.linalg.norm(_adjacency(before, residue_nodes) - _adjacency(after, residue_nodes))
        ),
        triangle_change=float(abs(_triangle_count(before, residue_nodes) - _triangle_count(after, residue_nodes))),
        path_length_change=abs(
            _mean_shortest_path(before, residue_nodes, disconnected_distance)
            - _mean_shortest_path(after, residue_nodes, disconnected_distance)
        ),
        lambda2_change=abs(lambda2_after - lambda2_before),
        spectrum_change=float(np.sum((before_eigenvalues - after_eigenvalues) ** 2))
        if np.isclose(lambda2_before, lambda2_after)
        else 0.0,
        laplacian_change=float(np.linalg.norm(after_laplacian - before_laplacian)),
        betweenness_change=_mean_abs_difference(
            _betweenness_centrality(before, residue_nodes),
            _betweenness_centrality(after, residue_nodes),
            residue_nodes,
        ),
        diameter_change=abs(
            _diameter(before, residue_nodes, disconnected_distance)
            - _diameter(after, residue_nodes, disconnected_distance)
        ),
        edge_distance_change=_edge_distance_change(before, after, residue_nodes),
        geometry_angle_penalty=_geometry_angle_penalty(
            after,
            ideal_metal_angles=ideal_metal_angles or {},
            default_ideal_angle=default_ideal_angle,
        ),
        metal_betweenness=_metal_betweenness(after),
    )

    component_weights = {field.name: 1.0 for field in fields(TRSComponents)}
    if weights:
        unknown = set(weights) - set(component_weights)
        if unknown:
            raise ValueError(f"Unknown TRS weight(s): {sorted(unknown)}")
        component_weights.update(weights)

    total = sum(getattr(components, name) * weight for name, weight in component_weights.items())
    return TRSResult(total=float(total), components=components, weights=component_weights)


def calculate_3d_trs(
    before: AtomStructure | str,
    after: AtomStructure | str,
    weights: Mapping[str, float] | None = None,
    metal_elements: Iterable[str] = ("Ca", "Zn", "Mg", "Fe", "Cu", "Mn", "Co", "Ni"),
    donor_elements: Iterable[str] = ("O", "N", "S"),
    contact_cutoff: float = 4.5,
    metal_cutoff: float = 3.0,
    default_ideal_angle: float | None = None,
) -> TRSResult:
    """Calculate TRS directly from exact 3D atom-coordinate tables.

    This keeps the input close to the CCDC-style table. Each atom is a node with
    its exact coordinates. Edges are inferred from 3D distances:

    - metal-donor edges use ``metal_cutoff``
    - other heavy-atom contacts use ``contact_cutoff``
    - hydrogen-hydrogen contacts are ignored
    """

    before_structure = AtomStructure.from_table(before) if isinstance(before, str) else before
    after_structure = AtomStructure.from_table(after) if isinstance(after, str) else after
    before_structure_model = structure_from_3d_coordinates(
        before_structure,
        metal_elements=metal_elements,
        donor_elements=donor_elements,
        contact_cutoff=contact_cutoff,
        metal_cutoff=metal_cutoff,
    )
    after_structure_model = structure_from_3d_coordinates(
        after_structure,
        metal_elements=metal_elements,
        donor_elements=donor_elements,
        contact_cutoff=contact_cutoff,
        metal_cutoff=metal_cutoff,
    )
    return calculate_trs(
        before_structure_model,
        after_structure_model,
        weights=weights,
        default_ideal_angle=default_ideal_angle,
    )


def structure_from_3d_coordinates(
    structure: AtomStructure,
    metal_elements: Iterable[str] = ("Ca", "Zn", "Mg", "Fe", "Cu", "Mn", "Co", "Ni"),
    donor_elements: Iterable[str] = ("O", "N", "S"),
    contact_cutoff: float = 4.5,
    metal_cutoff: float = 3.0,
) -> ProteinStructure:
    """Build an atom-level contact structure from exact 3D coordinates."""

    metal_set = {element.capitalize() for element in metal_elements}
    donor_set = {element.capitalize() for element in donor_elements}
    metal_nodes = {
        atom.node_id
        for atom in structure.atoms
        if atom.element in metal_set
    }
    coordinates = {atom.node_id: atom.coordinate for atom in structure.atoms}
    edges: set[Edge] = set()

    for atom_a, atom_b in combinations(structure.atoms, 2):
        if atom_a.element == "H" and atom_b.element == "H":
            continue

        atom_distance = dist(atom_a.coordinate, atom_b.coordinate)
        a_is_metal = atom_a.node_id in metal_nodes
        b_is_metal = atom_b.node_id in metal_nodes

        if a_is_metal or b_is_metal:
            donor = atom_b if a_is_metal else atom_a
            if donor.element in donor_set and atom_distance <= metal_cutoff:
                edges.add(_normalize_edge(atom_a.node_id, atom_b.node_id))
        elif atom_distance <= contact_cutoff:
            edges.add(_normalize_edge(atom_a.node_id, atom_b.node_id))

    return ProteinStructure.from_edges(
        nodes=coordinates,
        edges=edges,
        coordinates=coordinates,
        metal_nodes=metal_nodes,
    )


def _normalize_edge(a: NodeId, b: NodeId) -> Edge:
    return (a, b) if repr(a) <= repr(b) else (b, a)


def _shared_residue_nodes(before: ProteinStructure, after: ProteinStructure) -> list[NodeId]:
    return sorted(before.residue_nodes & after.residue_nodes, key=repr)


def _mean_abs_difference(
    before_values: Mapping[NodeId, float],
    after_values: Mapping[NodeId, float],
    nodes: Sequence[NodeId],
) -> float:
    return sum(abs(after_values.get(node, 0.0) - before_values.get(node, 0.0)) for node in nodes) / len(nodes)


def _adjacency(structure: ProteinStructure, nodes: Sequence[NodeId]) -> np.ndarray:
    index = {node: i for i, node in enumerate(nodes)}
    matrix = np.zeros((len(nodes), len(nodes)), dtype=float)
    for a, b in structure.edges:
        if a in index and b in index:
            matrix[index[a], index[b]] = 1.0
            matrix[index[b], index[a]] = 1.0
    return matrix


def _laplacian(structure: ProteinStructure, nodes: Sequence[NodeId]) -> np.ndarray:
    adjacency = _adjacency(structure, nodes)
    degree = np.diag(adjacency.sum(axis=1))
    return degree - adjacency


def _lambda2(eigenvalues: np.ndarray) -> float:
    return float(eigenvalues[1]) if len(eigenvalues) > 1 else 0.0


def _clustering_coefficients(structure: ProteinStructure, nodes: Sequence[NodeId]) -> dict[NodeId, float]:
    neighbors = structure.neighbors()
    coefficients: dict[NodeId, float] = {}
    for node in nodes:
        residue_neighbors = [n for n in neighbors.get(node, set()) if n in structure.residue_nodes]
        possible = len(residue_neighbors) * (len(residue_neighbors) - 1) / 2
        if possible == 0:
            coefficients[node] = 0.0
            continue
        actual = sum(1 for a, b in combinations(residue_neighbors, 2) if structure.has_edge(a, b))
        coefficients[node] = actual / possible
    return coefficients


def _triangle_count(structure: ProteinStructure, nodes: Sequence[NodeId]) -> int:
    count = 0
    node_set = set(nodes)
    for a, b, c in combinations(nodes, 3):
        if a in node_set and structure.has_edge(a, b) and structure.has_edge(a, c) and structure.has_edge(b, c):
            count += 1
    return count


def _coordination_number(structure: ProteinStructure) -> int:
    neighbors = structure.neighbors()
    coordinated_residues: set[NodeId] = set()
    for metal in structure.metal_nodes:
        coordinated_residues.update(neighbors.get(metal, set()) & structure.residue_nodes)
    return len(coordinated_residues)


def _all_pairs_shortest_paths(structure: ProteinStructure, nodes: Sequence[NodeId]) -> dict[NodeId, dict[NodeId, int]]:
    adjacency = structure.neighbors()
    allowed = set(nodes)
    distances: dict[NodeId, dict[NodeId, int]] = {}
    for source in nodes:
        seen = {source: 0}
        queue: deque[NodeId] = deque([source])
        while queue:
            node = queue.popleft()
            for neighbor in adjacency.get(node, set()) & allowed:
                if neighbor not in seen:
                    seen[neighbor] = seen[node] + 1
                    queue.append(neighbor)
        distances[source] = seen
    return distances


def _mean_shortest_path(
    structure: ProteinStructure,
    nodes: Sequence[NodeId],
    disconnected_distance: float | None,
) -> float:
    if len(nodes) < 2:
        return 0.0
    penalty = disconnected_distance if disconnected_distance is not None else float(len(nodes))
    distances = _all_pairs_shortest_paths(structure, nodes)
    values = []
    for a, b in combinations(nodes, 2):
        values.append(float(distances[a].get(b, penalty)))
    return sum(values) / len(values)


def _diameter(
    structure: ProteinStructure,
    nodes: Sequence[NodeId],
    disconnected_distance: float | None,
) -> float:
    if len(nodes) < 2:
        return 0.0
    penalty = disconnected_distance if disconnected_distance is not None else float(len(nodes))
    distances = _all_pairs_shortest_paths(structure, nodes)
    return max(float(distances[a].get(b, penalty)) for a, b in combinations(nodes, 2))


def _betweenness_centrality(structure: ProteinStructure, nodes: Sequence[NodeId]) -> dict[NodeId, float]:
    """Brandes betweenness centrality for unweighted undirected structures."""

    adjacency = {node: structure.neighbors().get(node, set()) & set(nodes) for node in nodes}
    centrality = dict.fromkeys(nodes, 0.0)

    for source in nodes:
        stack: list[NodeId] = []
        predecessors: dict[NodeId, list[NodeId]] = {node: [] for node in nodes}
        sigma = dict.fromkeys(nodes, 0.0)
        sigma[source] = 1.0
        distance = dict.fromkeys(nodes, -1)
        distance[source] = 0
        queue: deque[NodeId] = deque([source])

        while queue:
            vertex = queue.popleft()
            stack.append(vertex)
            for neighbor in adjacency[vertex]:
                if distance[neighbor] < 0:
                    queue.append(neighbor)
                    distance[neighbor] = distance[vertex] + 1
                if distance[neighbor] == distance[vertex] + 1:
                    sigma[neighbor] += sigma[vertex]
                    predecessors[neighbor].append(vertex)

        dependency = dict.fromkeys(nodes, 0.0)
        while stack:
            vertex = stack.pop()
            for pred in predecessors[vertex]:
                dependency[pred] += (sigma[pred] / sigma[vertex]) * (1.0 + dependency[vertex])
            if vertex != source:
                centrality[vertex] += dependency[vertex]

    scale = 1.0 / 2.0
    if len(nodes) > 2:
        scale /= (len(nodes) - 1) * (len(nodes) - 2) / 2
    return {node: value * scale for node, value in centrality.items()}


def _edge_distance_change(before: ProteinStructure, after: ProteinStructure, residue_nodes: Sequence[NodeId]) -> float:
    if not before.coordinates or not after.coordinates:
        return 0.0

    residue_edges = {
        edge
        for edge in before.edges | after.edges
        if edge[0] in residue_nodes
        and edge[1] in residue_nodes
        and edge[0] in before.coordinates
        and edge[1] in before.coordinates
        and edge[0] in after.coordinates
        and edge[1] in after.coordinates
    }
    if not residue_edges:
        return 0.0

    total = 0.0
    for a, b in residue_edges:
        before_distance = dist(before.coordinates[a], before.coordinates[b])
        after_distance = dist(after.coordinates[a], after.coordinates[b])
        total += abs(after_distance - before_distance)
    return total / len(residue_edges)


def _geometry_angle_penalty(
    structure: ProteinStructure,
    ideal_metal_angles: Mapping[NodeId, float],
    default_ideal_angle: float | None,
) -> float:
    if not structure.coordinates:
        return 0.0

    neighbors = structure.neighbors()
    penalty = 0.0
    for metal in structure.metal_nodes:
        if metal not in structure.coordinates:
            continue
        ideal_angle = ideal_metal_angles.get(metal, default_ideal_angle)
        if ideal_angle is None:
            continue
        residue_neighbors = [
            node
            for node in neighbors.get(metal, set()) & structure.residue_nodes
            if node in structure.coordinates
        ]
        for a, b in combinations(residue_neighbors, 2):
            penalty += abs(_angle(structure.coordinates[a], structure.coordinates[metal], structure.coordinates[b]) - ideal_angle)
    return penalty


def _angle(a: Coordinate, center: Coordinate, b: Coordinate) -> float:
    vector_a = np.array(a, dtype=float) - np.array(center, dtype=float)
    vector_b = np.array(b, dtype=float) - np.array(center, dtype=float)
    denominator = np.linalg.norm(vector_a) * np.linalg.norm(vector_b)
    if denominator == 0:
        return 0.0
    cosine = float(np.dot(vector_a, vector_b) / denominator)
    return degrees(acos(max(-1.0, min(1.0, cosine))))


def _metal_betweenness(structure: ProteinStructure) -> float:
    if not structure.metal_nodes:
        return 0.0
    nodes = sorted(structure.nodes, key=repr)
    centrality = _betweenness_centrality(structure, nodes)
    return sum(centrality.get(metal, 0.0) for metal in structure.metal_nodes) / len(structure.metal_nodes)
