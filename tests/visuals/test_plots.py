import random
from itertools import chain

import networkx as nx
import numpy as np
import pandas as pd
import pytest

import pyciemss
from pyciemss.integration_utils.result_processing import convert_to_output_format
from pyciemss.visuals import plots, vega


def by_key_value(targets, key, value):
    for entry in targets:
        if entry[key] == value:
            return entry


def make_nice_labels(labels):
    """Utility for generally-nice-labels for testing purposes."""
    return {
        k: "_".join(k.split("_")[:-1])
        for k in labels
        if "_" in k and k not in ["sample_id", "timepoint_id", "timepoint_notional"]
    }


class TestTrajectory:
    @staticmethod
    @pytest.fixture
    def distributions():
        model_1_path = (
            "https://raw.githubusercontent.com/DARPA-ASKEM/simulation-integration"
            "/main/data/models/SEIRHD_NPI_Type1_petrinet.json"
        )
        start_time = 0.0
        end_time = 100.0
        logging_step_size = 1
        num_samples = 30
        sample = pyciemss.sample(
            model_1_path,
            end_time,
            logging_step_size,
            num_samples,
            start_time=start_time,
            solver_method="euler",
        )["unprocessed_result"]

        for e in sample.values():
            if len(e.shape) > 1:
                num_timepoints = e.shape[1]

        return convert_to_output_format(
            sample,
            timepoints=np.linspace(start_time, end_time, num_timepoints),
            time_unit="notional",
        )

    @staticmethod
    @pytest.fixture
    def traces(distributions):
        return (
            distributions[distributions["sample_id"] == 0]
            .set_index("timepoint_notional")[["dead_observable_state", "I_state"]]
            .rename(
                columns={
                    "dead_observable_state": "dead_exemplar",
                    "I_state": "I_exemplar",
                }
            )
        )

    @staticmethod
    @pytest.fixture
    def observed_points(traces):
        return traces.iloc[::10]

    def test_base(self, distributions):
        schema = plots.trajectories(distributions)

        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])
        assert {"trajectory", "timepoint", "lower", "upper"} == set(df.columns)

    def test_rename(self, distributions):
        nice_labels = make_nice_labels(distributions.columns)

        schema = plots.trajectories(distributions, relabel=nice_labels)

        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])

        kept_names = df["trajectory"].unique()
        for name in nice_labels.values():
            assert name in kept_names, f"Nice name '{name}' not found"

        for name in nice_labels.keys():
            assert name not in kept_names, "Bad name unexpectedly found"

    def test_keep(self, distributions):
        schema = plots.trajectories(distributions, keep=".*_observable.*")
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])

        kept = sorted(df["trajectory"].unique())
        assert [
            "dead_observable_state",
            "exposed_observable_state",
            "hospitalized_observable_state",
            "infected_observable_state",
        ] == kept, f"Keeping by regex failed.  Kept {kept}"

        keep_list = ["dead_observable_state", "exposed_observable_state"]
        schema = plots.trajectories(distributions, keep=keep_list)
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])
        assert keep_list == sorted(df["trajectory"].unique()), "Keeping by list"

        nice_labels = make_nice_labels(distributions.columns)
        schema = plots.trajectories(
            distributions,
            relabel=nice_labels,
            keep=keep_list,
        )
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])
        assert ["dead_observable", "exposed_observable"] == sorted(
            df["trajectory"].unique()
        ), "Rename after keeping"

    def test_keep_drop(self, distributions):
        assert (
            "H_state" in distributions.columns
        ), "Expected trajectory not found in pre-test"

        should_keep = [
            p
            for p in distributions.columns
            if "_state" not in p
            and p not in ["sample_id", "timepoint_id", "timepoint_notional"]
        ]
        should_drop = [p for p in distributions.columns if "_state" in p]

        assert len(should_keep) > 0, "Expected keep trajectories not found in pre-test"
        assert len(should_drop) > 0, "Expected drop trajectories not found in pre-test"

        schema = plots.trajectories(distributions, keep=".*_.*", drop=".*_state")
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])

        kept = df["trajectory"].unique()
        for name in should_keep:
            assert name in kept, f"Unexpectedly lost '{name}'"

        for name in should_drop:
            assert name not in kept, f"Unexpectedly kept '{name}'"

    def test_drop(self, distributions):
        assert (
            "H_state" in distributions.columns
        ), "Exepected trajectory not found in pre-test"

        should_drop = [p for p in distributions.columns if "_observable_state" in p]
        assert len(should_drop) > 0, "Exepected trajectory not found in pre-test"

        schema = plots.trajectories(distributions, drop=should_drop)
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])

        assert (
            "H_state" in df["trajectory"].unique()
        ), "Exepected trajectory not retained from list"
        assert (
            "D_state" in df["trajectory"].unique()
        ), "Exepected trajectory not retained from list"

        for t in should_drop:
            assert (
                t not in df["trajectory"]
            ), "Trajectory still present after drop from list"

        try:
            schema = plots.trajectories(distributions, drop="THIS IS NOT HERE")
        except Exception:
            assert False, "Error dropping non-existent trajectory"

        schema = plots.trajectories(distributions, drop=".*_observable")
        df = pd.DataFrame(vega.find_named(schema["data"], "distributions")["values"])
        assert (
            "E_state" in df["trajectory"].unique()
        ), "Exepected trajectory not retained from pattern"
        assert (
            "R_state" in df["trajectory"].unique()
        ), "Exepected trajectory not retained from pattern"
        assert (
            "hospitalized_observable_state" not in df["trajectory"].unique()
        ), "Trajectory still present after drop from pattern"

    def test_points(self, distributions, observed_points):
        schema = plots.trajectories(
            distributions,
            keep=".*_state",
            points=observed_points,
        )

        points = pd.DataFrame(vega.find_named(schema["data"], "points")["values"])

        assert len(points["trajectory"].unique()) == 2, "Unexpected number of exemplars"

        assert (
            len(points) == observed_points.count().sum()
        ), "Unexpected number of exemplar points"

    def test_traces(self, distributions, traces):
        schema = plots.trajectories(
            distributions,
            keep=".*_state",
            traces=traces,
        )

        shown_traces = pd.DataFrame(vega.find_named(schema["data"], "traces")["values"])
        plots.save_schema(schema, "_schema.json")

        assert sorted(traces.columns.unique()) == sorted(
            shown_traces["trajectory"].unique()
        ), "Unexpected traces"

        for exemplar in shown_traces["trajectory"].unique():
            assert len(traces) == len(
                shown_traces[shown_traces["trajectory"] == exemplar]
            ), "Unexpected number of trace data points"


class TestHistograms:
    @staticmethod
    @pytest.fixture
    def simulation_result():
        model_1_path = (
            "https://raw.githubusercontent.com/DARPA-ASKEM/simulation-integration/"
            "main/data/models/SEIRHD_NPI_Type1_petrinet.json"
        )
        start_time = 0.0
        end_time = 100.0
        logging_step_size = 10.0
        num_samples = 3

        return pyciemss.sample(
            model_1_path,
            end_time,
            logging_step_size,
            num_samples,
            start_time=start_time,
            solver_method="euler",
        )["unprocessed_result"]

    def test_histogram(self, simulation_result):
        hist, bins = plots.histogram_multi(
            D=simulation_result["D_state"], return_bins=True
        )

        bins = bins.reset_index()

        assert all(bins["bin0"].value_counts() == 1), "Duplicated bins found"
        assert all(bins["bin1"].value_counts() == 1), "Duplicated bins found"

        hist_data = pd.DataFrame(by_key_value(hist["data"], "name", "binned")["values"])
        assert all(bins == hist_data), "Bin count not as expected"

        assert (
            len(by_key_value(hist["data"], "name", "xref")["values"]) == 0
        ), "Missing xrefs"
        assert (
            len(by_key_value(hist["data"], "name", "yref")["values"]) == 0
        ), "Missing yrefs"

    def test_histogram_empty_refs(self, simulation_result):
        xrefs = []
        yrefs = []
        hist = plots.histogram_multi(
            D=simulation_result["D_state"],
            xrefs=xrefs,
            yrefs=yrefs,
            return_bins=False,
        )

        assert (
            len(by_key_value(hist["data"], "name", "xref")["values"]) == 0
        ), "xrefs found when not expected"
        assert (
            len(by_key_value(hist["data"], "name", "yref")["values"]) == 0
        ), "yrefs found when not expected"

    @pytest.mark.parametrize("num_refs", range(1, 20))
    def test_histogram_refs(self, num_refs, simulation_result):
        xrefs = [*range(num_refs)]
        yrefs = [*range(num_refs)]
        hist = plots.histogram_multi(
            D=simulation_result["D_state"],
            xrefs=xrefs,
            yrefs=yrefs,
            return_bins=False,
        )

        assert num_refs == len(
            by_key_value(hist["data"], "name", "xref")["values"]
        ), "Nonzero xrefs not as expected"
        assert num_refs == len(
            by_key_value(hist["data"], "name", "yref")["values"]
        ), "Nonzero yrefs not as expected"

        hist = plots.histogram_multi(
            D=simulation_result["D_state"],
            xrefs=xrefs,
            yrefs=[],
            return_bins=False,
        )

        assert num_refs == len(
            by_key_value(hist["data"], "name", "xref")["values"]
        ), "Nonzero xrefs not as expected when there are zero yrefs"
        assert (
            len(by_key_value(hist["data"], "name", "yref")["values"]) == 0
        ), "Zero yrefs not as expected when there are nonzero xrefs"

        hist = plots.histogram_multi(
            D=simulation_result["D_state"],
            xrefs=[],
            yrefs=yrefs,
            return_bins=False,
        )

        assert (
            len(by_key_value(hist["data"], "name", "xref")["values"]) == 0
        ), "Zero xrefs not as expected when there are nonzero yrefs"
        assert num_refs == len(
            by_key_value(hist["data"], "name", "yref")["values"]
        ), "Nonzero yrefs not as expected when there are zero xrefs"

    def test_histogram_multi(self, simulation_result):
        hist = plots.histogram_multi(
            D=simulation_result["D_state"],
            E=simulation_result["E_state"],
            H=simulation_result["R_state"],
        )
        data = pd.DataFrame(by_key_value(hist["data"], "name", "binned")["values"])
        assert set(data["label"].values) == {"D", "E", "H"}

        hist = plots.histogram_multi(
            D_state=simulation_result["D_state"],
            E_state=simulation_result["E_state"],
        )
        data = pd.DataFrame(by_key_value(hist["data"], "name", "binned")["values"])
        assert set(data["label"].values) == {"D_state", "E_state"}


class TestHeatmapScatter:
    def test_implicit_heatmap(self):
        df = pd.DataFrame(3 * np.random.random((100, 2)), columns=["test4", "test5"])
        schema = plots.heatmap_scatter(df, max_x_bins=4, max_y_bins=4)

        points = vega.find_named(schema["data"], "points")["values"]
        assert all(pd.DataFrame(points) == df), "Unexpected points values found"

    def test_explicit_heatmap(self):
        def create_fake_data():
            nx, ny = (10, 10)
            x = np.linspace(0, 10, nx)
            y, a = np.linspace(0, 10, ny, retstep=True)

            # create mesh data
            xv, yv = np.meshgrid(x, y)
            zz = xv**2 + yv**2

            # create scatter plot
            df = pd.DataFrame(
                10 * np.random.random((100, 2)), columns=["alpha", "gamma"]
            )
            return (xv, yv, zz), df

        mesh_data, scatter_data = create_fake_data()
        schema = plots.heatmap_scatter(scatter_data, mesh_data)

        points = vega.find_named(schema["data"], "points")["values"]
        assert all(
            pd.DataFrame(points) == scatter_data
        ), "Unexpected points values found"

        mesh = pd.DataFrame(vega.find_named(schema["data"], "mesh")["values"])
        assert mesh.size == 500, "Unexpected mesh representation size."
        assert all(mesh["__count"].isin(mesh_data[2].ravel())), "Unexpected count found"


class TestGraph:
    @staticmethod
    @pytest.fixture
    def test_graph():
        def rand_attributions():
            possible = "ABCD"
            return random.sample(possible, random.randint(1, len(possible)))

        def rand_label():
            possible = "TUVWXYZ"
            return random.randint(1, 10)
            return random.sample(possible, 1)[0]

        g = nx.generators.barabasi_albert_graph(5, 3)
        node_properties = {
            n: {"attribution": rand_attributions(), "label": rand_label()}
            for n in g.nodes()
        }

        edge_attributions = {e: {"attribution": rand_attributions()} for e in g.edges()}

        nx.set_node_attributes(g, node_properties)
        nx.set_edge_attributes(g, edge_attributions)
        return g

    def test_multigraph(self, test_graph):
        uncollapsed = plots.attributed_graph(test_graph)
        nodes = vega.find_named(uncollapsed["data"], "node-data")["values"]
        edges = vega.find_named(uncollapsed["data"], "link-data")["values"]
        assert len(test_graph.nodes) == len(nodes), "Nodes issue in conversion"
        assert len(test_graph.edges) == len(edges), "Edges issue in conversion"

        all_attributions = set(
            chain(*nx.get_node_attributes(test_graph, "attribution").values())
        )
        nx.set_node_attributes(test_graph, {0: {"attribution": all_attributions}})
        collapsed = plots.attributed_graph(test_graph, collapse_all=True)
        nodes = vega.find_named(collapsed["data"], "node-data")["values"]
        edges = vega.find_named(collapsed["data"], "link-data")["values"]
        assert len(test_graph.nodes) == len(
            nodes
        ), "Nodes issue in conversion (collapse-case)"
        assert len(test_graph.edges) == len(
            edges
        ), "Edges issue in conversion (collapse-case)"
        assert [["*all*"]] == [
            n["attribution"] for n in nodes if n["label"] == 0
        ], "All tag not found as expected"

    def test_springgraph(self, test_graph):
        schema = plots.spring_force_graph(test_graph, node_labels="label")
        nodes = vega.find_named(schema["data"], "node-data")["values"]
        edges = vega.find_named(schema["data"], "link-data")["values"]
        assert len(test_graph.nodes) == len(nodes), "Nodes issue in conversion"
        assert len(test_graph.edges) == len(edges), "Edges issue in conversion"

    def test_provided_layout(self, test_graph):
        pos = nx.fruchterman_reingold_layout(test_graph)
        schema = plots.spring_force_graph(test_graph, node_labels="label", layout=pos)

        nodes = vega.find_named(schema["data"], "node-data")["values"]
        edges = vega.find_named(schema["data"], "link-data")["values"]
        assert len(test_graph.nodes) == len(nodes), "Nodes issue in conversion"
        assert len(test_graph.edges) == len(edges), "Edges issue in conversion"

        for id, (x, y) in pos.items():
            n = [n for n in nodes if n["label"] == id][0]
            assert n["inputX"] == x, f"Layout lost for {id}"
            assert n["inputY"] == y, f"Layout lost for {id}"
