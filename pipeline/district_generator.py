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

# required for gerrychain reproducibility
os.environ.setdefault("PYTHONHASHSEED", "0")

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

    # Step 3 — Output directory
    output_dir = Path(config["gerrychain_output_dir"] + f"/{n_district}")
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

    # Create the Markov chain
    chain = MarkovChain(
        proposal=recom_proposal,
        constraints=constraints,
        accept=always_accept,
        initial_state=initial_partition,
        total_steps=chain_length,
    )

    output_path = (
            output_dir / 
            f"{n_district}_districts.jsonl.gz"
        )
    
    metadata_chain = {
        "geodata_path":config["geodata_path"],
        "population_column": population_column,
        "chain_length": chain_length,
        "epsilon":  seed_epsilon,
        "seed": config['seed']
    }
    
    with open(Path(str(output_path).replace(".jsonl.gz",".json")),"w") as f:
         json.dump(metadata_chain,f)
    
    with gzip.open(
            output_path, mode="wt", encoding="utf-8"
        ) as gz_file:
            writer = jl.Writer(gz_file)
            for sample_num, step in enumerate(
                tqdm(
                    chain,
                    total=chain_length,
                    desc=f"{n_district} districts"
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
            writer.close()
     

