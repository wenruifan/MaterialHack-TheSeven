import unittest

from trs import AtomStructure, ProteinStructure, calculate_3d_trs, calculate_trs, structure_from_3d_coordinates


class TRSScoreTests(unittest.TestCase):
    def test_trs_returns_components_and_weighted_total(self):
        before = ProteinStructure.from_edges(
            edges=[("A", "B"), ("B", "C")],
            coordinates={
                "A": (0.0, 0.0, 0.0),
                "B": (1.0, 0.0, 0.0),
                "C": (2.0, 0.0, 0.0),
            },
        )
        after = ProteinStructure.from_edges(
            edges=[("A", "B"), ("B", "C"), ("B", "ZN"), ("C", "ZN"), ("A", "C")],
            coordinates={
                "A": (0.0, 0.0, 0.0),
                "B": (1.0, 0.0, 0.0),
                "C": (2.2, 0.0, 0.0),
                "ZN": (1.5, 0.5, 0.0),
            },
            metal_nodes=["ZN"],
        )

        result = calculate_trs(before, after, default_ideal_angle=109.5)

        self.assertGreater(result.total, 0.0)
        self.assertEqual(result.components.coordination_number, 2.0)
        self.assertGreater(result.components.adjacency_change, 0.0)
        self.assertGreater(result.components.edge_distance_change, 0.0)

    def test_weights_can_disable_terms(self):
        before = ProteinStructure.from_edges(edges=[("A", "B")])
        after = ProteinStructure.from_edges(edges=[("A", "B"), ("A", "ZN")], metal_nodes=["ZN"])

        weighted = calculate_trs(
            before,
            after,
            weights={
                "degree_change": 0.0,
                "coordination_number": 0.0,
                "metal_betweenness": 0.0,
            },
        )

        self.assertEqual(weighted.components.coordination_number, 1.0)
        self.assertEqual(weighted.total, 0.0)

    def test_coordinates_without_interaction_do_not_create_connectivity(self):
        before = ProteinStructure.from_edges(
            nodes=["A", "B"],
            edges=[],
            coordinates={
                "A": (0.0, 0.0, 0.0),
                "B": (0.1, 0.0, 0.0),
            },
        )
        after = ProteinStructure.from_edges(
            nodes=["A", "B"],
            edges=[],
            coordinates={
                "A": (0.0, 0.0, 0.0),
                "B": (5.0, 0.0, 0.0),
            },
        )

        result = calculate_trs(before, after)

        self.assertEqual(result.total, 0.0)
        self.assertEqual(result.components.adjacency_change, 0.0)
        self.assertEqual(result.components.path_length_change, 0.0)
        self.assertEqual(result.components.edge_distance_change, 0.0)

    def test_calcium_atom_table_can_build_exact_3d_contact_structure(self):
        atom_table = """
        1 NZ 40.2641 50.4415 35.4633 N.4 6 LYS293 1.0000
        2 OD1 45.2442 46.0964 33.4119 O.co2 4 ASP276 0.0000
        3 CA 44.1881 48.2622 34.3408 Ca 7 CA506 0.0000
        """

        structure = AtomStructure.from_table(atom_table)
        contact_structure = structure_from_3d_coordinates(structure, metal_cutoff=3.0)

        self.assertEqual(len(structure.atoms), 3)
        self.assertIn("CA506:CA:3", contact_structure.metal_nodes)
        self.assertIn(("ASP276:OD1:2", "CA506:CA:3"), contact_structure.edges)
        self.assertNotIn(("CA506:CA:3", "LYS293:NZ:1"), contact_structure.edges)

    def test_calculate_3d_trs_uses_atom_coordinates_directly(self):
        before = """
        1 OD1 0.0 0.0 0.0 O.co2 1 ASP276 0.0000
        2 NZ 8.0 0.0 0.0 N.4 2 LYS293 1.0000
        """
        after = """
        1 OD1 0.0 0.0 0.0 O.co2 1 ASP276 0.0000
        2 NZ 8.0 0.0 0.0 N.4 2 LYS293 1.0000
        3 CA 2.4 0.0 0.0 Ca 3 CA506 0.0000
        """

        result = calculate_3d_trs(before, after, metal_cutoff=3.0)

        self.assertGreater(result.total, 0.0)
        self.assertEqual(result.components.coordination_number, 1.0)


if __name__ == "__main__":
    unittest.main()
