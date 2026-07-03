# Ensemble of Redistricting Plans using Recom

Why Ensemble Redistricting? 
Because we are going to create thousands of pausible legally-valid plans of a same-size district.

How do we achieve this?

The script `district_generator.py` receives the configurations file in a json format where it contains all the parameters needed.

Example:

```
{
  "run_name": "Kansas_City_Baseline", <-- Name of the simulation

  "geodata_path": "./data/KC_blocks_vap_cvap.gpkg", <-- Path of the geodata at block level

  "gerrychain_output_dir": "outputs/districts/chain_out", <-- Path of folder to store MC results

  "population_column": "total_pop_20", <-- Name of the total population column in the geodata
  "population_vap_column": "total_vap_20",
  "pop_of_interest_column": "white_vap_20", <-- Name of the VAP column in the geodata

  "chain_length": 1000,  <-- Important parameter! Number of steps of MC or plans to simulate

  "num_subsamples": 5,  <-- Determines the number of districting plans to select from all the simulated ones

  "total_seats": 12,

  "epsilon":  0.01, <-- Margin error of the initial partition to create. Input for the MC

  "voting_configs": {
    "STV": { "n_seats": 3, "tiebreak": "random" }
  },

  "district_configs": [
      { "num_districts": 4, "winners": 3 }
  ],  <-- District size

  "turnout": { "A": 1.0, "B": 1.0},

  "num_voters": 1000,

  "slate_to_candidates": {
    "A": ["A1", "A2", "A3", "A4", "A5"],
    "B": ["B1", "B2", "B3", "B4", "B5"]
  },

  "focal_group": "A",

  "cohesion_parameters": {
    "A": { "A": 0.6, "B": 0.4 },
    "B": { "A": 0.4, "B": 0.6 }
  },

  "alphas": {
    "A": { "A": 0.5, "B": 2 },
    "B": { "A": 1, "B": 1 }
  },

  "num_reps": 2,
  
  "seed": 42
}
```

1. Transform geodata to dual graph.
- We represent Kansas City as a graph where each node corresponds to a precint/ block, joined by an edge for adjancent units. This is also a districting plan where we have an assignment of every node to a district number. The adjacent nodes are geographically adjacent precints/ blocks.

2. Initial Random Partition
- Recursive tree splitting algorithm defines a starting plan. In other words, we are going to generate a concrete assignment of precints/blocks into districts that satifies population balance rules. So, MC have an input to where to start its random wlaks