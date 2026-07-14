# First batch of libraries
import geopandas as gpd
import json
import jsonlines as jl
import gzip
from pathlib import Path
from tqdm import tqdm


from functools import partial
import networkx as nx
import os
import random
# from typing import Optional

# Libraries GerryChain
from gerrychain import Graph, Partition, MarkovChain
from gerrychain.updaters import Tally, cut_edges
from gerrychain.accept import always_accept
from gerrychain.proposals import recom
from gerrychain.constraints import within_percent_of_ideal_population
from gerrychain.tree import bipartition_tree
from gerrychain.optimization import Gingleator

from pipeline.utils.helpers import ensemble_signature, get_chain_out_dir, get_district_images_dir, save_district_plan_png

# required for gerrychain reproducibility
os.environ.setdefault("PYTHONHASHSEED", "0")

# Maps a config's "optimize_for_bloc" value to the per-district VAP tally updater
# alias set up below (and paired with the "VAP20" total to form each district's
# minority share for the Gingleator).
BLOC_TO_VAP_ALIAS = {
    "W": "WVAP20",
    "B": "BVAP20",
    "H": "HVAP20",
    "A": "AVAP20",
}

def generate_districts(config):
    """
    Run a recom markov chain for each district count
    and write sampled plans to gzipped jsonl files.
    
    Outputs:
        outputs/{run_name}/districts/{run_name}_{n}_districts.jsonl.gz
        Each line: {"assignment": [...], "sample": n}
    """
    
    # Define seed
    random.seed(config['seed'])

    # Set parameters
    run_name = config["run_name"]
    population_column = config["population_column"]
    chain_length = config["chain_length"]
    n_district = config["district_configs"][0]["num_districts"]
    seed_epsilon = config["epsilon"]
    chain_epsilon = 0.05

    print(f"District Number: {n_district}\n")
    print(f"Voting Rule: {config["voting_configs"]}\n")

    # Import data
    geodata_path = Path(config["geodata_path"])
    gdf = gpd.read_file(geodata_path)
    
    # Data stats
    print(f"Number of precints: {gdf.shape[0]}\n")
    print(f"Number of columns: {gdf.shape[1]}\n")

    # Transform geopandas to graph object
    # graph_path = geodata_path.parent / (
    #     geodata_path.stem + "_graph.json"
    # )
    graph = Graph.from_geodataframe(gdf)

    print(f"Number of nodes: {len(graph.nodes)}")
    print(f"Number of edges: {len(graph.edges)}")

    # Quick trick to make sure that if the node labels are not integers, then
    # they are converted to integers starting from 0 so that saving to a JSONL
    # file works correctly every time.
    graph = Graph.from_networkx(
        nx.convert_node_labels_to_integers(graph, first_label=0)
    )

    # Save graph
    output_dir = Path(f"outputs/{run_name}/graph")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    graph_path = output_dir / f"{run_name}_graph.json"
    graph.to_json(str(graph_path))

    # Step 3 — Output directory (keyed by ensemble signature so equivalent runs
    # share a chain and different ensembles stay separate)
    output_dir = get_chain_out_dir(ensemble_signature(config), n_district)
    output_dir.mkdir(parents=True, exist_ok=True)

    updaters = {
        "population": Tally(population_column,alias = "population"),
        "cut_edges": cut_edges,
        "POCVAP20": Tally("poc_vap_20", "POCVAP20"),
        "VAP20": Tally("total_vap_20", "VAP20"),
        "WVAP20": Tally("white_vap_20", "WVAP20"),
        "BVAP20": Tally("bvap_20", "BVAP20"),
        "HVAP20": Tally("hvap_20", "HVAP20"),
        "AVAP20": Tally("asian_nhpi_vap_20", "AVAP20")
    }

    # Create an initial partition
    initial_partition = Partition.from_random_assignment(
        graph=graph,
        n_parts=n_district,
        epsilon=seed_epsilon,
        pop_col=population_column,
        updaters=updaters,
    )

    target_population = sum(initial_partition["population"].values()) / len(initial_partition)

    constraints = [
        within_percent_of_ideal_population(initial_partition, chain_epsilon)
    ]

    recom_proposal = partial(
        recom,
        pop_col=population_column,
        pop_target=target_population,
        epsilon=chain_epsilon
    )

    # Choose the ensemble strategy: a neutral ReCom chain by default, or a
    # Gingleator short-burst optimizer when the config asks to optimize a bloc's
    # opportunity districts via "optimize_for_bloc".
    optimize_bloc = config.get("optimize_for_bloc")
    if optimize_bloc:
        vap_alias = BLOC_TO_VAP_ALIAS.get(optimize_bloc)
        if vap_alias is None:
            raise ValueError(
                f"optimize_for_bloc={optimize_bloc!r} must be one of "
                f"{sorted(BLOC_TO_VAP_ALIAS)}."
            )
        threshold = config.get("optimize_threshold", 0.10)
        burst_length = config.get("burst_length", 10)
        num_bursts = chain_length // burst_length
        total_steps = burst_length * num_bursts

        optimizer = Gingleator(
            recom_proposal,
            constraints,
            initial_partition,
            minority_pop_col=vap_alias,
            total_pop_col="VAP20",
            threshold=threshold,
            score_function=Gingleator.reward_partial_dist,
        )

        # short_bursts yields every observed partition (burst_length * num_bursts
        # total); each burst restarts from the best-scoring plan found so far.
        plan_stream = optimizer.short_bursts(burst_length, num_bursts)
        chain_desc = (
            f"{n_district} districts (short bursts, optimize {optimize_bloc} @ {threshold:.0%})"
        )
        print(
            f"Optimizing for bloc '{optimize_bloc}' via short bursts: "
            f"{num_bursts} bursts x {burst_length} steps = {total_steps} plans, "
            f"threshold={threshold:.0%}\n"
        )
    else:
        plan_stream = MarkovChain(
            proposal=recom_proposal,
            constraints=constraints,
            accept=always_accept,
            initial_state=initial_partition,
            total_steps=chain_length,
        )
        total_steps = chain_length
        chain_desc = f"{n_district} districts"

    output_path = (
            output_dir / 
            f"{n_district}_districts.jsonl.gz"
        )
    
    metadata_chain = {
        "geodata_path":config["geodata_path"],
        "population_column": population_column,
        "chain_length": chain_length,
        "epsilon":  seed_epsilon,
        "seed": config['seed'],
        "optimize_for_bloc": optimize_bloc,
    }
    
    with open(Path(str(output_path).replace(".jsonl.gz",".json")),"w") as f:
         json.dump(metadata_chain,f)

    # Export a PNG of each subsampled plan, on the same cadence the downstream
    # steps use (sample_idx % subsample_interval == 0, sample_idx being the
    # 0-based chain index), so images line up 1:1 with the settings files.
    num_subsamples = config["num_subsamples"]
    subsample_interval = max(1, chain_length // num_subsamples)
    images_dir = get_district_images_dir(run_name, n_district)

    with gzip.open(
            output_path, mode="wt", encoding="utf-8"
        ) as gz_file:
            writer = jl.Writer(gz_file)
            for sample_num, step in enumerate(
                tqdm(
                    plan_stream,
                    total=total_steps,
                    desc=chain_desc
                ),
                start=1
            ):
                assignment = list(
                    step.assignment.to_series().sort_index()
                )
                writer.write({
                    "assignment": assignment,
                    "sample": sample_num
                })

                sample_idx = sample_num - 1
                if sample_idx % subsample_interval == 0:
                    save_district_plan_png(
                        gdf,
                        assignment,
                        images_dir / (
                            f"{run_name}_{n_district}_district_plan_"
                            f"{sample_idx:03d}.png"
                        ),
                        title=f"{run_name} — {n_district} districts (plan {sample_idx:03d})",
                    )
            writer.close()
     

